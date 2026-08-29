"""Query building. No database connection is made — only the SQL is inspected."""
from argparse import Namespace
from datetime import date, timedelta

import pytest

from src import query

DEFAULTS = dict(
    field=None, level=None, funding=None, country=None, region=None,
    ielts_max=None, deadline_days=None, open=False, limit=50, csv=None,
)


def q(**over):
    return query.build_query(Namespace(**{**DEFAULTS, **over}))


def test_no_filters_produces_no_where_clause():
    sql, params = q()
    assert "WHERE" not in sql
    assert params == []


def test_open_filter():
    sql, params = q(open=True)
    assert "is_open = TRUE" in sql
    assert params == []


def test_field_uses_array_containment():
    sql, params = q(field="computer_science")
    assert "%s = ANY(fields)" in sql
    assert params == ["computer_science"]


def test_level_uses_array_containment():
    sql, params = q(level="masters")
    assert "%s = ANY(degree_levels)" in sql
    assert params == ["masters"]


def test_ielts_cap_keeps_rows_that_state_no_requirement():
    """Most DAAD pages never mention IELTS. Filtering on a cap must not silently
    hide every scholarship whose requirement is simply unstated."""
    sql, params = q(ielts_max=6.5)
    assert "ielts_min IS NULL OR ielts_min <= %s" in sql
    assert params == [6.5]


def test_deadline_window_is_bounded_at_both_ends():
    sql, params = q(deadline_days=30)
    assert "deadline BETWEEN %s AND %s" in sql
    assert params == [date.today(), date.today() + timedelta(days=30)]


def test_filters_combine_with_and_in_parameter_order():
    sql, params = q(field="computer_science", level="masters",
                    funding="fully_funded", region="Europe", ielts_max=6.5, open=True)
    assert sql.count(" AND ") == 5
    assert params == ["computer_science", "masters", "fully_funded", "Europe", 6.5]


def test_undated_scholarships_sort_last_not_first():
    # "deadline IS NULL" ordering keeps rolling/unknown entries from burying the
    # ones with a real, approaching deadline.
    sql, _ = q()
    assert "ORDER BY (deadline IS NULL), deadline ASC" in sql


def test_limit_is_coerced_to_an_integer():
    # LIMIT is the one value interpolated into the SQL string, so it must be an int.
    sql, _ = q(limit="10")
    assert "LIMIT 10" in sql


def test_non_numeric_limit_is_rejected_outright():
    # int() refusing is the safety property: nothing reaches the SQL string.
    with pytest.raises(ValueError):
        q(limit="10; DROP TABLE scholarships")


def test_values_are_parameterised_never_inlined():
    sql, params = q(country="'; DROP TABLE scholarships; --")
    assert "DROP" not in sql
    assert params == ["'; DROP TABLE scholarships; --"]


# --- saved searches (PLAN phase 4) ----------------------------------------------

def _args(**over):
    return Namespace(**{**DEFAULTS, "saved": None, "list_saved": False, **over})


def test_every_saved_search_has_help_text_and_builds_valid_sql():
    for name in query.SAVED_QUERIES:
        a = _args(saved=name)
        query.apply_saved(a)
        sql, params = query.build_query(a)
        assert query.SAVED_QUERIES[name]["help"]
        assert sql.startswith("SELECT")
        assert sql.count("%s") == len(params), f"{name}: placeholder/param mismatch"


def test_saved_search_fills_its_filters():
    a = _args(saved="cs_masters")
    query.apply_saved(a)
    assert a.field == "computer_science"
    assert a.level == "masters"
    assert a.funding == "fully_funded"
    assert a.open is True


def test_explicit_flags_are_not_overwritten_by_a_saved_search():
    # --saved cs_masters --level bachelors should narrow, not fight, the preset.
    a = _args(saved="cs_masters", level="bachelors")
    query.apply_saved(a)
    assert a.level == "bachelors"
    assert a.field == "computer_science"   # still filled from the preset


def test_unknown_saved_search_exits_with_a_helpful_message():
    with pytest.raises(SystemExit) as e:
        query.apply_saved(_args(saved="does_not_exist"))
    assert "cs_masters" in str(e.value)


def test_no_ielts_search_returns_rows_with_no_stated_requirement():
    a = _args(saved="no_ielts")
    query.apply_saved(a)
    sql, params = query.build_query(a)
    assert "ielts_min IS NULL OR ielts_min <= %s" in sql
    assert params == [0]
