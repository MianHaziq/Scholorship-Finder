"""Extraction rules — mostly guarding bugs that reached the real database once."""
from datetime import date

import pytest

from src import extract
from src.vocab import DEGREE_LEVELS, FIELDS

BASE = "https://www2.daad.de/deutschland/stipendium/datenbank/en/21148-scholarship-database/?detail=10000092"


# --- deadlines: never invent one ------------------------------------------------
#
# A page saying "deadlines differ and MAY be requested" once produced a real-looking
# 2026-05-29 (dateutil read "may" as May, day from today) and flag_expired() then hid
# the scholarship from --open queries. Prose must never be parsed.

@pytest.mark.parametrize(
    "raw",
    [
        "Application deadlines differ and may be requested at the individual institutions.",
        "Please contact the International Office of your home institution.",
        "varies",
        "31 October 2026",
        "Oct 2026",
        "rolling",
        "",
    ],
)
def test_prose_deadline_is_never_parsed(raw):
    assert extract._parse_deadline(None, raw) is None


@pytest.mark.parametrize(
    "iso,expected",
    [
        ("2026-10-31", date(2026, 10, 31)),
        ("  2026-10-31  ", date(2026, 10, 31)),
        ("October 2026", None),     # ambiguous -> reject
        ("not stated", None),
        ("unknown", None),
        ("", None),
        (None, None),
    ],
)
def test_only_strict_iso_dates_are_accepted(iso, expected):
    assert extract._parse_deadline(iso, None) == expected


def test_iso_wins_and_prose_is_ignored_even_when_both_present():
    assert extract._parse_deadline("2026-10-31", "sometime in May") == date(2026, 10, 31)


# --- urls -----------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        # Pages give a bare host; urljoin would graft it onto the source path.
        ("daad.de/go/en/stipa10000092", "https://daad.de/go/en/stipa10000092"),
        ("https://daad.de/go/x", "https://daad.de/go/x"),
        ("http://example.org/a", "http://example.org/a"),
        ("//cdn.example.org/x", "https://cdn.example.org/x"),
        ("/deutschland/apply", "https://www2.daad.de/deutschland/apply"),
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_normalize_url(raw, expected):
    assert extract._normalize_url(raw, BASE) == expected


def test_filename_is_relative_not_a_hostname():
    # "apply.html" has a dot but is a file, not a domain.
    assert extract._normalize_url("apply.html", BASE).endswith(
        "/21148-scholarship-database/apply.html"
    )


# --- tag merging ----------------------------------------------------------------

def test_merge_tags_unions_feed_and_llm_preserving_order():
    assert extract._merge_tags(["masters"], ["phd"], DEGREE_LEVELS) == ["masters", "phd"]


def test_merge_tags_dedupes():
    assert extract._merge_tags(["masters"], ["masters"], DEGREE_LEVELS) == ["masters"]


def test_merge_tags_drops_values_outside_the_vocabulary():
    assert extract._merge_tags(["nonsense"], ["masters"], DEGREE_LEVELS) == ["masters"]


def test_merge_tags_handles_missing_seed():
    assert extract._merge_tags(None, ["engineering"], FIELDS) == ["engineering"]


# --- extract() merge behaviour --------------------------------------------------

SOURCE = {"id": "daad", "region": "Europe", "hint": ""}

LLM_REPLY = {
    "is_scholarship": True,
    "title": "AI title that should lose to the feed",
    "provider": "German National Academic Foundation",
    "country": "Germany",
    "degree_levels": ["masters"],
    "fields": ["computer_science"],
    "field_raw": "Maths and Natural Sciences",
    "funding_type": "fully_funded",
    "funding_details": "stipend",
    "ielts_required": True,
    "ielts_min": "6.5",
    "other_language": "German B2",
    "deadline_raw": "deadlines differ and may be requested",
    "deadline_iso": None,
    "apply_url": "daad.de/go/en/stipa1",
    "summary": "A summary.",
    "eligibility": "Graduates.",
}


@pytest.fixture
def stub_llm(monkeypatch):
    def _stub(reply):
        monkeypatch.setattr(extract.llm, "complete_json", lambda prompt: reply)
    return _stub


def test_seed_title_overrides_the_model(stub_llm):
    stub_llm(LLM_REPLY)
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "Feed Title"})
    assert rec["title"] == "Feed Title"


def test_provider_comes_from_the_model_when_not_seeded(stub_llm):
    stub_llm(LLM_REPLY)
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "T"})
    assert rec["provider"] == "German National Academic Foundation"


def test_levels_and_fields_are_unioned_with_the_seed(stub_llm):
    stub_llm(LLM_REPLY)
    rec = extract.extract(
        "page text", SOURCE, BASE,
        seed={"title": "T", "degree_levels": ["bachelors"], "fields": ["natural_sciences"]},
    )
    assert rec["degree_levels"] == ["bachelors", "masters"]
    # The feed cannot know "computer_science"; only the page text supplies it.
    assert rec["fields"] == ["natural_sciences", "computer_science"]


def test_prose_deadline_leaves_the_record_open(stub_llm):
    stub_llm(LLM_REPLY)
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "T"})
    assert rec["deadline"] is None
    assert rec["is_open"] is True


def test_apply_url_is_absolute(stub_llm):
    stub_llm(LLM_REPLY)
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "T"})
    assert rec["apply_url"].startswith("https://")


def test_seeded_record_survives_is_scholarship_false(stub_llm):
    # It came out of the source's own scholarship feed, so a thin page must not
    # let the model talk us out of it.
    stub_llm({**LLM_REPLY, "is_scholarship": False})
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "Feed Title"})
    assert rec is not None and rec["title"] == "Feed Title"


def test_unseeded_record_is_dropped_when_not_a_scholarship(stub_llm):
    stub_llm({**LLM_REPLY, "is_scholarship": False})
    assert extract.extract("page text", SOURCE, BASE) is None


def test_record_without_any_title_is_dropped(stub_llm):
    stub_llm({**LLM_REPLY, "title": ""})
    assert extract.extract("page text", SOURCE, BASE) is None


def test_invalid_funding_type_falls_back_to_unknown(stub_llm):
    stub_llm({**LLM_REPLY, "funding_type": "somewhat_funded"})
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "T"})
    assert rec["funding_type"] == "unknown"


def test_ielts_min_is_coerced_to_a_number(stub_llm):
    stub_llm(LLM_REPLY)
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "T"})
    assert rec["ielts_min"] == 6.5


def test_unparseable_ielts_min_becomes_null(stub_llm):
    stub_llm({**LLM_REPLY, "ielts_min": "six point five"})
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "T"})
    assert rec["ielts_min"] is None


def test_region_is_derived_from_country(stub_llm):
    stub_llm(LLM_REPLY)
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "T"})
    assert rec["region"] == "Europe"


def test_external_id_is_carried_through_for_dedupe(stub_llm):
    stub_llm(LLM_REPLY)
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "T", "external_id": 10000092})
    assert rec["external_id"] == 10000092


def test_past_deadline_closes_the_record(stub_llm):
    stub_llm({**LLM_REPLY, "deadline_iso": "2020-01-01"})
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "T"})
    assert rec["deadline"] == date(2020, 1, 1)
    assert rec["is_open"] is False


def test_feed_summary_is_only_a_fallback(stub_llm):
    stub_llm(LLM_REPLY)
    rec = extract.extract("p", SOURCE, BASE, seed={"title": "T", "summary": "truncated feed intro"})
    assert rec["summary"] == "A summary."

    stub_llm({**LLM_REPLY, "summary": None})
    rec = extract.extract("p", SOURCE, BASE, seed={"title": "T", "summary": "truncated feed intro"})
    assert rec["summary"] == "truncated feed intro"


# --- region must not be inherited from the source when the country is known -----
#
# DAAD lists genuine non-German destinations (Canon Foundation funds research IN
# JAPAN; "Sur-Place"/"In-Region" programmes study in Mexico/Jordan). Inheriting the
# source's region labelled those "Europe", hiding that they are out of scope v1.

def test_known_but_unmapped_country_gets_no_region(stub_llm):
    stub_llm({**LLM_REPLY, "country": "Japan"})
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "T"})
    assert rec["country"] == "Japan"
    assert rec["region"] is None


def test_source_region_is_only_a_fallback_for_unknown_country(stub_llm):
    stub_llm({**LLM_REPLY, "country": None})
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "T"})
    assert rec["region"] == "Europe"


def test_mapped_country_still_derives_its_own_region(stub_llm):
    stub_llm({**LLM_REPLY, "country": "Canada"})
    rec = extract.extract("page text", SOURCE, BASE, seed={"title": "T"})
    assert rec["region"] == "North America"


# --- seeds from a hand-listed (static) source ------------------------------------

def test_seeded_country_overrides_the_page(stub_llm):
    # Chevening pages talk mostly about the applicant's home country; config knows
    # the destination is the UK.
    stub_llm({**LLM_REPLY, "country": "Pakistan"})
    rec = extract.extract("p", SOURCE, BASE, seed={"title": "T", "country": "UK"})
    assert rec["country"] == "UK"
    assert rec["region"] == "UK"


def test_seeded_funding_type_overrides_the_page(stub_llm):
    stub_llm({**LLM_REPLY, "funding_type": "unknown"})
    rec = extract.extract("p", SOURCE, BASE,
                          seed={"title": "T", "funding_type": "fully_funded"})
    assert rec["funding_type"] == "fully_funded"


def test_invalid_seeded_funding_type_still_falls_back(stub_llm):
    stub_llm(LLM_REPLY)
    rec = extract.extract("p", SOURCE, BASE, seed={"title": "T", "funding_type": "gold"})
    assert rec["funding_type"] == "unknown"


def test_extracted_country_is_canonicalised(stub_llm):
    stub_llm({**LLM_REPLY, "country": "United Kingdom"})
    rec = extract.extract("p", SOURCE, BASE, seed={"title": "T"})
    assert rec["country"] == "UK"
    assert rec["region"] == "UK"
