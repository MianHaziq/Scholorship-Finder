"""Dedupe fingerprint rules. No database connection is made."""
from src import db


def test_external_id_fingerprint_survives_ai_rewording():
    """The whole point: a reworded title must UPDATE, not insert a duplicate.

    The old fingerprint hashed title+provider+deadline_raw, all AI-derived, so any
    drift between daily runs would silently create a second row for one scholarship.
    """
    run1 = {
        "source_id": "daad", "external_id": 10000092,
        "title": "Prussian Cultural Heritage Foundation: Grant Programme",
        "provider": "SPK", "deadline_raw": "31 October 2026",
    }
    run2 = {
        "source_id": "daad", "external_id": 10000092,
        "title": "SPK Grant Programme",
        "provider": "Prussian Cultural Heritage",
        "deadline_raw": "Oct 31, 2026",
    }
    assert db.fingerprint(run1) == db.fingerprint(run2)


def test_different_external_ids_never_collide():
    a = db.fingerprint({"source_id": "daad", "external_id": 1})
    b = db.fingerprint({"source_id": "daad", "external_id": 2})
    assert a != b


def test_same_external_id_in_different_sources_never_collides():
    a = db.fingerprint({"source_id": "daad", "external_id": 1})
    b = db.fingerprint({"source_id": "chevening", "external_id": 1})
    assert a != b


def test_int_and_str_external_ids_agree():
    a = db.fingerprint({"source_id": "daad", "external_id": 10000092})
    b = db.fingerprint({"source_id": "daad", "external_id": "10000092"})
    assert a == b


def test_fallback_is_used_when_no_external_id():
    a = db.fingerprint({"source_id": "x", "title": "A", "provider": "P", "deadline_raw": "d"})
    b = db.fingerprint({"source_id": "x", "title": "B", "provider": "P", "deadline_raw": "d"})
    assert a != b


def test_fallback_is_stable_and_case_insensitive():
    a = db.fingerprint({"source_id": "x", "title": "Alpha", "provider": "P", "deadline_raw": "d"})
    b = db.fingerprint({"source_id": "x", "title": "  alpha  ", "provider": "p", "deadline_raw": "D"})
    assert a == b


def test_fallback_tolerates_missing_fields():
    assert db.fingerprint({"source_id": "x", "title": "Only a title"})


def test_fingerprint_fits_the_column():
    fp = db.fingerprint({"source_id": "daad", "external_id": 1})
    assert len(fp) == 32 and fp.isalnum()
