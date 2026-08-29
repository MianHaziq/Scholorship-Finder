-- Scholarship Finder — database schema (Postgres / Neon)
-- Run once against your DATABASE_URL:  psql "$DATABASE_URL" -f schema.sql
-- (or the healthcheck / init_db step runs it for you)

CREATE TABLE IF NOT EXISTS scholarships (
    id                BIGSERIAL PRIMARY KEY,
    source_id         TEXT NOT NULL,          -- which source config produced this
    source_url        TEXT NOT NULL,          -- page it came from
    fingerprint       TEXT UNIQUE NOT NULL,   -- hash(title+provider+deadline) for dedupe

    title             TEXT NOT NULL,
    provider          TEXT,
    country           TEXT,
    region            TEXT,

    degree_levels     TEXT[] DEFAULT '{}',    -- {bachelors, masters, phd}
    fields            TEXT[] DEFAULT '{}',    -- normalized tags (see src/vocab.py)
    field_raw         TEXT,

    funding_type      TEXT,                   -- fully_funded | partial | unknown
    funding_details   TEXT,

    ielts_required    BOOLEAN,                -- null = unknown
    ielts_min         NUMERIC,
    other_language    TEXT,

    deadline          DATE,                   -- null = rolling/unknown
    deadline_raw      TEXT,
    is_open           BOOLEAN DEFAULT TRUE,

    apply_url         TEXT,
    summary           TEXT,
    eligibility       TEXT,

    first_seen        TIMESTAMPTZ DEFAULT now(),
    last_seen         TIMESTAMPTZ DEFAULT now(),
    last_verified     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_country  ON scholarships(country);
CREATE INDEX IF NOT EXISTS idx_levels   ON scholarships USING GIN(degree_levels);
CREATE INDEX IF NOT EXISTS idx_fields   ON scholarships USING GIN(fields);
CREATE INDEX IF NOT EXISTS idx_funding  ON scholarships(funding_type);
CREATE INDEX IF NOT EXISTS idx_deadline ON scholarships(deadline);
CREATE INDEX IF NOT EXISTS idx_open     ON scholarships(is_open);

-- Track each pipeline run for visibility / debugging.
CREATE TABLE IF NOT EXISTS run_log (
    id          BIGSERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ,
    fetched     INT DEFAULT 0,
    new_rows    INT DEFAULT 0,
    updated     INT DEFAULT 0,
    expired     INT DEFAULT 0,
    errors      INT DEFAULT 0,
    notes       TEXT
);
