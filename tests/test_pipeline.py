"""Budget rotation and vocabulary. No network, no database."""
from datetime import datetime, timedelta, timezone

import pytest

from src import config, db, pipeline, vocab
from src.discover import Candidate


class FakeSession:
    """Stands in for db.Session: returns a canned last-seen index."""

    def __init__(self, index):
        self._index = index

    def run(self, fn, *args, **kwargs):
        return dict(self._index)


def _cand(ext_id):
    return Candidate(url=f"https://e.org/?detail={ext_id}", seed={"external_id": ext_id})


def _fp(ext_id):
    return db.fingerprint({"source_id": "daad", "external_id": ext_id})


SRC = {"id": "daad"}


def test_never_seen_candidates_come_first():
    now = datetime.now(timezone.utc)
    cands = [_cand(1), _cand(2), _cand(3)]
    index = {_fp(1): now, _fp(2): now}          # 3 has never been seen
    ordered = pipeline._prioritise(FakeSession(index), SRC, cands)
    assert ordered[0].seed["external_id"] == 3


def test_seen_candidates_are_ordered_oldest_first():
    now = datetime.now(timezone.utc)
    cands = [_cand(1), _cand(2), _cand(3)]
    index = {
        _fp(1): now,
        _fp(2): now - timedelta(days=5),        # stalest
        _fp(3): now - timedelta(days=1),
    }
    ordered = pipeline._prioritise(FakeSession(index), SRC, cands)
    assert [c.seed["external_id"] for c in ordered] == [2, 3, 1]


def test_budget_rotates_through_the_whole_catalogue():
    """A fixed feed order + a budget smaller than the catalogue would refresh the
    same first N forever and never reach the tail. This is that regression test."""
    budget = 40
    total = 103
    cands = [_cand(i) for i in range(total)]
    index = {}
    covered = set()

    for day in range(3):
        ordered = pipeline._prioritise(FakeSession(index), SRC, cands)
        batch = ordered[:budget]
        stamp = datetime.now(timezone.utc) - timedelta(days=3 - day)
        for c in batch:
            covered.add(c.seed["external_id"])
            index[_fp(c.seed["external_id"])] = stamp

    assert len(covered) == total, "3 runs of 40 must cover all 103 programmes"


def test_first_run_processes_only_unseen_and_never_repeats():
    cands = [_cand(i) for i in range(103)]
    ordered = pipeline._prioritise(FakeSession({}), SRC, cands)
    ids = [c.seed["external_id"] for c in ordered[:40]]
    assert len(set(ids)) == 40


def test_prioritise_falls_back_to_feed_order_when_the_db_is_unreachable():
    class Broken:
        def run(self, *a, **k):
            raise RuntimeError("db down")

    cands = [_cand(1), _cand(2)]
    assert pipeline._prioritise(Broken(), SRC, cands) == cands


def test_candidates_without_external_id_are_matched_by_url():
    now = datetime.now(timezone.utc)
    c1 = Candidate(url="https://e.org/a")
    c2 = Candidate(url="https://e.org/b")
    ordered = pipeline._prioritise(FakeSession({"https://e.org/a": now}), SRC, [c1, c2])
    assert ordered[0].url == "https://e.org/b"      # unseen first


# --- vocabulary -----------------------------------------------------------------

@pytest.mark.parametrize(
    "country,region",
    [
        ("Germany", "Europe"),
        ("UK", "UK"),
        ("United Kingdom", "UK"),
        ("Canada", "North America"),
        ("Australia", "Oceania"),
        ("Europe", "Europe"),
        ("  Germany  ", "Europe"),
        ("Atlantis", None),
        (None, None),
    ],
)
def test_region_for(country, region):
    assert vocab.region_for(country) == region


def test_vocabularies_have_no_duplicates():
    for name in ("FIELDS", "DEGREE_LEVELS", "FUNDING_TYPES"):
        values = getattr(vocab, name)
        assert len(values) == len(set(values)), f"{name} has duplicates"


def test_budget_is_configured():
    assert config.MAX_PAGES_PER_RUN > 0


# --- a silently dead source must be reported -------------------------------------

def test_dead_sources_flags_a_source_that_stored_nothing():
    per_source = {
        "daad": {"candidates": 102, "fetched": 102, "new": 0, "updated": 102, "errors": 0},
        "chevening": {"candidates": 0, "fetched": 0, "new": 0, "updated": 0, "errors": 1},
    }
    assert pipeline.dead_sources(per_source) == ["chevening"]


def test_a_source_that_only_updates_is_not_considered_dead():
    per_source = {"daad": {"candidates": 5, "fetched": 5, "new": 0, "updated": 5, "errors": 0}}
    assert pipeline.dead_sources(per_source) == []


def test_a_source_that_only_inserts_is_not_considered_dead():
    per_source = {"x": {"candidates": 5, "fetched": 5, "new": 5, "updated": 0, "errors": 0}}
    assert pipeline.dead_sources(per_source) == []


def test_all_sources_dead_are_all_reported():
    per_source = {
        "a": {"candidates": 0, "fetched": 0, "new": 0, "updated": 0, "errors": 1},
        "b": {"candidates": 3, "fetched": 3, "new": 0, "updated": 0, "errors": 3},
    }
    assert sorted(pipeline.dead_sources(per_source)) == ["a", "b"]


# --- country names must be canonical, or filters split one destination in two ----

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("United Kingdom", "UK"),
        ("united kingdom", "UK"),
        ("  Great Britain ", "UK"),
        ("England", "UK"),
        ("The Netherlands", "Netherlands"),
        ("Holland", "Netherlands"),
        ("Deutschland", "Germany"),
        ("European Union", "Europe"),
        ("Germany", "Germany"),      # already canonical
        ("Japan", "Japan"),          # unknown names pass through untouched
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_canonical_country(raw, expected):
    assert vocab.canonical_country(raw) == expected


def test_canonical_names_resolve_to_a_region():
    # "United Kingdom" alone had no region; canonicalising is what makes it map.
    assert vocab.region_for(vocab.canonical_country("United Kingdom")) == "UK"
    assert vocab.region_for(vocab.canonical_country("Holland")) == "Europe"


def test_aliases_never_map_onto_another_alias():
    for target in set(vocab.COUNTRY_ALIASES.values()):
        assert target.lower() not in vocab.COUNTRY_ALIASES, (
            f"{target} is both an alias target and an alias key"
        )
