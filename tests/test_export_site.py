"""Static site generation. No database: rows are supplied directly."""
import json
import re

import pytest

from src import export_site

ROWS = [
    {"title": "Alpha Masters Grant", "provider": "DAAD", "country": "Germany",
     "region": "Europe", "degree_levels": ["masters"], "fields": ["computer_science"],
     "field_raw": None, "funding_type": "fully_funded", "funding_details": "stipend",
     "ielts_required": True, "ielts_min": 6.5, "other_language": None,
     "deadline": "2026-10-31", "deadline_raw": "31 Oct", "is_open": True,
     "apply_url": "https://example.org/a", "summary": "A grant.",
     "eligibility": "Graduates", "source_id": "daad", "last_seen": "2026-08-29"},
    {"title": "Beta Bachelors Award", "provider": "Chevening", "country": "UK",
     "region": "UK", "degree_levels": ["bachelors"], "fields": ["any_field"],
     "field_raw": None, "funding_type": "partial", "funding_details": None,
     "ielts_required": None, "ielts_min": None, "other_language": None,
     "deadline": None, "deadline_raw": None, "is_open": True,
     "apply_url": "https://example.org/b", "summary": None,
     "eligibility": None, "source_id": "chevening", "last_seen": "2026-08-29"},
]


def payload():
    return export_site.build_payload(ROWS)


def embedded(html):
    m = re.search(r"window\.__SCHOLARSHIPS__ = (.*?);</script>", html, re.S)
    return json.loads(m.group(1).replace("\u003c", "<"))


# --- payload --------------------------------------------------------------------

def test_facets_only_offer_values_that_exist():
    f = payload()["facets"]
    assert f["levels"] == ["bachelors", "masters"]        # no "phd": no such row
    assert f["funding"] == ["fully_funded", "partial"]    # no "unknown"
    assert f["countries"] == ["Germany", "UK"]


def test_levels_keep_vocabulary_order_not_alphabetical():
    # bachelors -> masters -> phd is meaningful; alphabetical would be arbitrary.
    assert payload()["facets"]["levels"] == ["bachelors", "masters"]


def test_fields_keep_vocabulary_order():
    rows = [dict(ROWS[0], fields=["engineering", "computer_science"])]
    assert export_site.build_payload(rows)["facets"]["fields"] == [
        "computer_science", "engineering"]


def test_payload_carries_today_so_the_page_can_compute_urgency():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload()["today"])


# --- rendering ------------------------------------------------------------------

def test_full_page_is_a_complete_document():
    html = export_site.render_html(payload())
    assert html.startswith("<!doctype html>")
    assert "<html lang=\"en\">" in html and "</html>" in html
    assert "<title>Scholarship Finder</title>" in html


def test_fragment_omits_the_document_wrapper():
    # Artifact publishing supplies its own <head>/<body>.
    html = export_site.render_html(payload(), fragment=True)
    assert "<!doctype" not in html.lower()
    assert "<html" not in html.lower() and "<body" not in html.lower()
    assert "<title>Scholarship Finder</title>" in html


def test_data_round_trips_into_the_page():
    data = embedded(export_site.render_html(payload()))
    assert len(data["rows"]) == 2
    assert data["rows"][0]["title"] == "Alpha Masters Grant"
    assert data["rows"][1]["deadline"] is None


def test_script_tags_in_data_cannot_break_out_of_the_block():
    """A title containing </script> would otherwise end the block early and
    inject raw markup into the page."""
    nasty = dict(ROWS[0], title="Evil </script><img src=x onerror=alert(1)>")
    html = export_site.render_html(export_site.build_payload([nasty]))
    assert "</script><img" not in html
    assert html.count("<script") == html.count("</script>")
    assert embedded(html)["rows"][0]["title"] == nasty["title"]


def test_both_themes_are_defined_at_token_level():
    html = export_site.render_html(payload())
    assert ":root {" in html
    assert "@media (prefers-color-scheme: dark)" in html
    assert ':root[data-theme="dark"]' in html
    assert ':root:not([data-theme="light"])' in html


def test_body_paints_its_own_background():
    # A transparent body borrows the host page's theme and can render
    # one theme's text on the other theme's ground.
    assert "background:var(--ground)" in export_site.render_html(payload())


def test_page_is_accessible_by_default():
    html = export_site.render_html(payload())
    assert "focus-visible" in html
    assert "prefers-reduced-motion" in html


def test_only_allowed_font_host_is_referenced():
    html = export_site.render_html(payload())
    hosts = set(re.findall(r'https?://([^/"\s]+)', html))
    external = {h for h in hosts if not h.startswith("example.org")}
    assert external <= {"fonts.googleapis.com", "fonts.gstatic.com"}, external
