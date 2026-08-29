"""Config-driven discovery. No network: the feed fetch is stubbed."""
import json

import pytest

from src import discover

TAFFY = 'var scholarships = TAFFY([{"id":1,"nameEn":"A"},{"id":2,"nameEn":"B"}]);'


# --- unwrapping a .js data file -------------------------------------------------

def test_unwrap_taffy():
    assert discover._unwrap(TAFFY, "taffy") == [
        {"id": 1, "nameEn": "A"}, {"id": 2, "nameEn": "B"},
    ]


def test_unwrap_plain_assignment():
    assert discover._unwrap('var x = [{"a":1}];', "assign") == [{"a": 1}]


def test_unwrap_bare_json():
    assert discover._unwrap('[{"a":1}]', None) == [{"a": 1}]


def test_unwrap_raises_on_garbage():
    with pytest.raises(ValueError):
        discover._unwrap("not a feed at all", "taffy")


# --- field access ---------------------------------------------------------------

def test_dig_reads_nested_paths():
    assert discover._dig({"introduction": {"en": "hello"}}, "introduction.en") == "hello"


def test_dig_returns_none_for_missing_hops():
    assert discover._dig({"a": 1}, "a.b.c") is None
    assert discover._dig({}, "nope") is None


# --- code -> vocabulary mapping -------------------------------------------------

LEVELS = {"from": "status", "values": {1: "bachelors", 3: "masters", 4: "phd"}}
SUBJECTS = {
    "from": "subjectGrps",
    "all_means": "any_field",
    "values": {
        "A": ["arts_humanities"],
        "B": ["social_sciences", "law", "business_economics"],
        "C": ["natural_sciences"],
    },
}


def test_map_translates_a_list_of_codes():
    assert discover._apply_map({"status": [1, 3]}, LEVELS) == ["bachelors", "masters"]


def test_map_translates_a_scalar_code():
    assert discover._apply_map({"status": 3}, LEVELS) == ["masters"]


def test_map_ignores_codes_with_no_mapping():
    # DAAD status 2/5 are postdoc/faculty — outside our vocabulary.
    assert discover._apply_map({"status": [2, 3, 5]}, LEVELS) == ["masters"]


def test_map_expands_one_code_to_several_tags():
    assert discover._apply_map({"subjectGrps": ["B"]}, SUBJECTS) == [
        "social_sciences", "law", "business_economics",
    ]


def test_map_dedupes_across_codes():
    spec = {"from": "g", "values": {"A": ["arts_humanities"], "G": ["arts_humanities"]}}
    assert discover._apply_map({"g": ["A", "G"]}, spec) == ["arts_humanities"]


def test_all_codes_present_means_open_to_everything():
    assert discover._apply_map({"subjectGrps": ["A", "B", "C"]}, SUBJECTS) == ["any_field"]


def test_map_of_missing_field_is_empty():
    assert discover._apply_map({}, LEVELS) == []


# --- scope filter ---------------------------------------------------------------

def test_require_any_keeps_in_scope_records():
    assert discover._passes({"status": [1, 2]}, {"status": [1, 3]}) is True


def test_require_any_rejects_out_of_scope_records():
    # postdoc/faculty only — not Bachelors/Masters.
    assert discover._passes({"status": [2, 5]}, {"status": [1, 3]}) is False


def test_require_any_matches_across_int_and_str():
    assert discover._passes({"status": ["3"]}, {"status": [3]}) is True


# --- end-to-end feed discovery (stubbed HTTP) -----------------------------------

FEED = json.dumps([
    {"sapProgid": 111, "nameEn": " Masters Programme ", "status": [1, 3],
     "subjectGrps": ["C"], "introduction": {"en": "Intro one."}},
    {"sapProgid": 222, "nameEn": "Postdoc Only", "status": [2],
     "subjectGrps": ["A"], "introduction": {"en": "Intro two."}},
    {"sapProgid": 333, "nameEn": "Law Programme", "status": [3],
     "subjectGrps": ["B"], "introduction": {"en": ""}},
])

SRC = {
    "id": "daad",
    "discovery": {
        "mode": "json_feed",
        "feed_url": "https://example.org/scholarships.js",
        "detail_url": "https://example.org/db/?detail={sapProgid}",
        "require_any": {"status": [1, 3]},
        "seed": {"external_id": "sapProgid", "title": "nameEn",
                 "summary": "introduction.en"},
        "map": {"degree_levels": LEVELS, "fields": SUBJECTS},
    },
}


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


@pytest.fixture
def stub_feed(monkeypatch):
    def _stub(body):
        monkeypatch.setattr(discover.httpx, "get", lambda *a, **k: _Resp(body))
    return _stub


def test_out_of_scope_records_are_filtered_before_any_llm_call(stub_feed):
    stub_feed(FEED)
    cands = discover.discover(SRC)
    assert [c.seed["external_id"] for c in cands] == [111, 333]


def test_detail_urls_are_built_from_the_template(stub_feed):
    stub_feed(FEED)
    assert discover.discover(SRC)[0].url == "https://example.org/db/?detail=111"


def test_seed_values_are_mapped_and_trimmed(stub_feed):
    stub_feed(FEED)
    seed = discover.discover(SRC)[0].seed
    assert seed["title"] == "Masters Programme"
    assert seed["degree_levels"] == ["bachelors", "masters"]
    assert seed["fields"] == ["natural_sciences"]
    assert seed["summary"] == "Intro one."


def test_empty_seed_strings_are_omitted(stub_feed):
    stub_feed(FEED)
    assert "summary" not in discover.discover(SRC)[1].seed


def test_exclude_ids_drops_known_non_scholarship_entries(stub_feed):
    # DAAD's "Important Information" is a portal stub that both the feed and the
    # LLM report as a real programme, so it can only be removed by hand.
    stub_feed(FEED)
    src = {**SRC, "discovery": {**SRC["discovery"], "exclude_ids": [111]}}
    assert [c.seed["external_id"] for c in discover.discover(src)] == [333]


def test_exclude_ids_matches_across_int_and_str(stub_feed):
    stub_feed(FEED)
    src = {**SRC, "discovery": {**SRC["discovery"], "exclude_ids": ["111"]}}
    assert 111 not in [c.seed["external_id"] for c in discover.discover(src)]


def test_no_exclude_ids_keeps_everything_in_scope(stub_feed):
    stub_feed(FEED)
    assert len(discover.discover(SRC)) == 2


def test_a_dead_feed_returns_no_candidates_instead_of_raising(stub_feed):
    def boom(*a, **k):
        raise RuntimeError("connection refused")
    stub_feed(FEED)
    discover.httpx.get = boom
    assert discover.discover(SRC) == []


def test_links_mode_is_the_default(monkeypatch):
    monkeypatch.setattr(discover.fetch, "get_html", lambda *a, **k: '<a href="/x/detail=1">x</a>')
    src = {"id": "s", "listing_urls": ["https://e.org/list"], "link_filter": "detail="}
    cands = discover.discover(src)
    assert len(cands) == 1
    # Link-mode has no feed id, so the URL becomes the dedupe identity.
    assert cands[0].seed == {"external_id": cands[0].url}


# --- static mode: sources that are ONE programme, not a database ----------------

STATIC_SRC = {
    "id": "chevening",
    "discovery": {
        "mode": "static",
        "urls": [
            {"url": "https://e.org/a", "country": "UK", "degree_levels": ["masters"]},
            "https://e.org/b",
            "https://e.org/a",          # duplicate, must collapse
        ],
    },
}


def test_static_mode_returns_the_listed_pages():
    cands = discover.discover(STATIC_SRC)
    assert [c.url for c in cands] == ["https://e.org/a", "https://e.org/b"]


def test_static_entries_carry_their_configured_seed():
    seed = discover.discover(STATIC_SRC)[0].seed
    assert seed["country"] == "UK"
    assert seed["degree_levels"] == ["masters"]


def test_static_url_becomes_the_stable_dedupe_id():
    # Without this the fingerprint would hash AI-written text and drift between runs.
    cands = discover.discover(STATIC_SRC)
    assert cands[0].seed["external_id"] == "https://e.org/a"
    assert cands[1].seed["external_id"] == "https://e.org/b"


def test_static_mode_makes_no_network_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("static mode must not fetch anything")
    monkeypatch.setattr(discover.fetch, "get_html", boom)
    monkeypatch.setattr(discover.httpx, "get", boom)
    assert len(discover.discover(STATIC_SRC)) == 2


def test_every_candidate_gets_a_stable_identity(stub_feed):
    """Two Commonwealth pages both extract to the title "Commonwealth Fellowships";
    without a URL identity they would hash to one row and lose a programme."""
    stub_feed(FEED)
    for c in discover.discover(SRC):
        assert c.seed.get("external_id")
    src = {"id": "s", "discovery": {"mode": "static",
                                    "urls": ["https://e.org/a", "https://e.org/b"]}}
    ids = [c.seed["external_id"] for c in discover.discover(src)]
    assert ids == ["https://e.org/a", "https://e.org/b"]


def test_feed_id_is_not_overwritten_by_the_url(stub_feed):
    stub_feed(FEED)
    assert discover.discover(SRC)[0].seed["external_id"] == 111
