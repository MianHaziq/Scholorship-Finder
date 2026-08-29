"""Postgres access: init, upsert scholarships, hygiene, run logging."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from . import config
from .vocab import region_for


def connect() -> psycopg.Connection:
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Fill it in your .env file.")
    return psycopg.connect(config.DATABASE_URL, row_factory=dict_row, autocommit=True)


class Session:
    """A database handle that survives Neon dropping the connection mid-run.

    Neon is serverless: it closes idle connections and scales computes to zero.
    A daily run spends most of its time waiting on HTTP and the LLM, so the
    connection sits idle for minutes at a time and WILL be closed underneath us.
    Holding one connection for the whole run meant a single drop turned every
    remaining page into "the connection is closed" and crashed the run at the end
    — one blip cost ~85 of 103 pages. So: reconnect and retry once per call.
    """

    def __init__(self) -> None:
        self._conn: psycopg.Connection | None = None

    @property
    def conn(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = connect()
        return self._conn

    def run(self, fn, *args, **kwargs):
        """Call fn(conn, ...), reconnecting once if the connection has died."""
        try:
            return fn(self.conn, *args, **kwargs)
        except psycopg.OperationalError as e:
            print(f"  [db] connection lost ({str(e).splitlines()[0]}); reconnecting")
            self.close()
            return fn(self.conn, *args, **kwargs)

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - already broken; nothing to salvage
                pass
        self._conn = None


def init_db() -> None:
    """Create tables/indexes from schema.sql (idempotent)."""
    sql = config.SCHEMA_FILE.read_text(encoding="utf-8")
    with connect() as conn:
        conn.execute(sql)


def fingerprint(rec: dict[str, Any]) -> str:
    """Stable dedupe key for a scholarship.

    Prefer the source's own permanent id (e.g. DAAD's sapProgid) when discovery
    supplied one: it survives the AI rewording a title or a deadline between runs.
    Falling back to title+provider+deadline means any such drift would insert a
    duplicate on the next daily run instead of updating the existing row.
    """
    external_id = rec.get("external_id")
    if external_id:
        key = f"{rec.get('source_id', '')}:{external_id}"
    else:
        key = "|".join(
            (rec.get(k) or "").strip().lower() if isinstance(rec.get(k), str) else ""
            for k in ("title", "provider", "deadline_raw")
        )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def upsert_scholarship(conn: psycopg.Connection, rec: dict[str, Any]) -> str:
    """Insert a new scholarship or refresh an existing one.

    Returns "new" or "updated". `rec` is the validated record from the extractor.
    """
    fp = fingerprint(rec)
    region = rec.get("region") or region_for(rec.get("country"))

    row = conn.execute(
        """
        INSERT INTO scholarships (
            source_id, source_url, fingerprint, title, provider, country, region,
            degree_levels, fields, field_raw, funding_type, funding_details,
            ielts_required, ielts_min, other_language,
            deadline, deadline_raw, is_open, apply_url, summary, eligibility
        ) VALUES (
            %(source_id)s, %(source_url)s, %(fingerprint)s, %(title)s, %(provider)s,
            %(country)s, %(region)s, %(degree_levels)s, %(fields)s, %(field_raw)s,
            %(funding_type)s, %(funding_details)s, %(ielts_required)s, %(ielts_min)s,
            %(other_language)s, %(deadline)s, %(deadline_raw)s, %(is_open)s,
            %(apply_url)s, %(summary)s, %(eligibility)s
        )
        ON CONFLICT (fingerprint) DO UPDATE SET
            source_url      = EXCLUDED.source_url,
            country         = EXCLUDED.country,
            region          = EXCLUDED.region,
            degree_levels   = EXCLUDED.degree_levels,
            fields          = EXCLUDED.fields,
            field_raw       = EXCLUDED.field_raw,
            funding_type    = EXCLUDED.funding_type,
            funding_details = EXCLUDED.funding_details,
            ielts_required  = EXCLUDED.ielts_required,
            ielts_min       = EXCLUDED.ielts_min,
            other_language  = EXCLUDED.other_language,
            deadline        = EXCLUDED.deadline,
            deadline_raw    = EXCLUDED.deadline_raw,
            apply_url       = EXCLUDED.apply_url,
            summary         = EXCLUDED.summary,
            eligibility     = EXCLUDED.eligibility,
            last_seen       = now(),
            last_verified   = now()
        RETURNING (xmax = 0) AS inserted
        """,
        {
            "source_id": rec["source_id"],
            "source_url": rec["source_url"],
            "fingerprint": fp,
            "title": rec["title"],
            "provider": rec.get("provider"),
            "country": rec.get("country"),
            "region": region,
            "degree_levels": rec.get("degree_levels") or [],
            "fields": rec.get("fields") or [],
            "field_raw": rec.get("field_raw"),
            "funding_type": rec.get("funding_type") or "unknown",
            "funding_details": rec.get("funding_details"),
            "ielts_required": rec.get("ielts_required"),
            "ielts_min": rec.get("ielts_min"),
            "other_language": rec.get("other_language"),
            "deadline": rec.get("deadline"),
            "deadline_raw": rec.get("deadline_raw"),
            "is_open": rec.get("is_open", True),
            "apply_url": rec.get("apply_url") or rec["source_url"],
            "summary": rec.get("summary"),
            "eligibility": rec.get("eligibility"),
        },
    ).fetchone()
    return "new" if row["inserted"] else "updated"


def last_seen_index(conn: psycopg.Connection, source_id: str) -> dict[str, Any]:
    """When each already-stored scholarship of this source was last refreshed.

    Keyed by both fingerprint and source_url so the pipeline can recognise a
    candidate before spending an LLM call on it.
    """
    rows = conn.execute(
        "SELECT fingerprint, source_url, last_seen FROM scholarships WHERE source_id = %s",
        (source_id,),
    ).fetchall()
    index: dict[str, Any] = {}
    for r in rows:
        index[r["fingerprint"]] = r["last_seen"]
        index[r["source_url"]] = r["last_seen"]
    return index


def flag_expired(conn: psycopg.Connection) -> int:
    """Mark scholarships whose deadline has passed as closed. Returns count changed."""
    row = conn.execute(
        """
        UPDATE scholarships
           SET is_open = FALSE
         WHERE is_open = TRUE
           AND deadline IS NOT NULL
           AND deadline < %s
        """,
        (date.today(),),
    )
    return row.rowcount


def start_run(conn: psycopg.Connection) -> int:
    row = conn.execute("INSERT INTO run_log DEFAULT VALUES RETURNING id").fetchone()
    return row["id"]


def finish_run(conn: psycopg.Connection, run_id: int, stats: dict[str, int], notes: str = "") -> None:
    conn.execute(
        """
        UPDATE run_log SET finished_at = now(), fetched = %s, new_rows = %s,
               updated = %s, expired = %s, errors = %s, notes = %s
         WHERE id = %s
        """,
        (
            stats.get("fetched", 0),
            stats.get("new", 0),
            stats.get("updated", 0),
            stats.get("expired", 0),
            stats.get("errors", 0),
            notes,
            run_id,
        ),
    )
