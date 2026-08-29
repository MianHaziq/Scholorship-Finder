# Scholarship Finder — Build Plan

> Personal, continuously-updated scholarship database. 100% free stack.
> Scope v1: **Germany + Europe, UK, Canada + Australia**. Bachelors + Masters, all fields.
> Owner: single user. Output for now: **a queryable database** (frontend is Phase 2).

---

## 1. Goal & Non-Goals

**Goal:** An automated engine that, on a daily schedule, discovers scholarships from a
curated list of trusted sources, uses a free AI model to turn messy web pages into clean
structured records, stores them in a free database, keeps them fresh (new ones in, expired
deadlines flagged), and lets me filter by field, degree level, funding type, IELTS, country.

**Non-goals (v1):**
- Not trying to capture *literally every* scholarship on earth (impossible; even paid products miss some).
- No USA sources in v1 (too fragmented — add later).
- No frontend UI in v1 (Phase 2).
- No multi-user / accounts / auth.

---

## 2. Core Design Decision

There is **no single free API** that lists all world scholarships (confirmed — even DAAD has
no public API/export). So the model is:

> **Curated source list  →  fetch pages  →  free AI extracts structured fields  →  dedupe + store  →  query/filter.**

The AI (not brittle hand-written parsers) does the hard extraction, so layout changes on
source sites break things far less often.

---

## 3. Tech Stack (all free tier, no credit card)

| Concern | Choice | Notes |
|---|---|---|
| Language | **Python 3.11+** | Best scraping + LLM ecosystem |
| Scheduler | **GitHub Actions cron** | Runs daily, exits — avoids "free servers sleep" problem entirely |
| Fetching | `httpx` + `selectolax`/`BeautifulSoup`; `Playwright` only if a site needs JS | Start simple |
| AI extraction | **Google Gemini free tier** (1M context) primary, **Groq** fallback | No card; reads whole pages; retry/rotate on rate limit |
| Database | **Neon (Postgres, 3 GB free)** | Permanent free tier; also works from GitHub Actions |
| Secrets | **GitHub Actions Secrets** | API keys never in code |
| Language of records | English | Translate non-EN pages during extraction |

**Why GitHub Actions over an always-on server:** free always-on hosts (Render, Supabase compute)
pause after inactivity. A scheduled job that wakes, works, and exits sidesteps that completely
and is more than enough for a once-a-day refresh.

---

## 4. Data Model (the heart of the system)

One main table `scholarships`. Designed so Phase-2 filters are trivial SQL.

```sql
CREATE TABLE scholarships (
    id                BIGSERIAL PRIMARY KEY,
    source_id         TEXT NOT NULL,          -- which source config produced this
    source_url        TEXT NOT NULL,          -- page it came from
    fingerprint       TEXT UNIQUE NOT NULL,   -- hash(title+provider+deadline) for dedupe

    title             TEXT NOT NULL,
    provider          TEXT,                   -- e.g. "DAAD", "University of X"
    country           TEXT,                   -- normalized: Germany, UK, Canada...
    region            TEXT,                   -- Europe / North America / Oceania

    degree_levels     TEXT[],                 -- {bachelors, masters, phd}
    fields            TEXT[],                 -- normalized tags: {computer_science, engineering...}
    field_raw         TEXT,                   -- original field text from the page

    funding_type      TEXT,                   -- fully_funded | partial | unknown
    funding_details   TEXT,                   -- what's covered: tuition, stipend, travel...

    ielts_required    BOOLEAN,                -- null = unknown
    ielts_min         NUMERIC,                -- e.g. 6.5 if stated
    other_language    TEXT,                   -- TOEFL/German level if mentioned

    deadline          DATE,                   -- null = rolling/unknown
    deadline_raw      TEXT,                   -- original text ("varies", "Oct 2026")
    is_open           BOOLEAN DEFAULT TRUE,   -- false when deadline passed

    apply_url         TEXT,
    summary           TEXT,                   -- 1-2 sentence AI summary
    eligibility       TEXT,

    first_seen        TIMESTAMPTZ DEFAULT now(),
    last_seen         TIMESTAMPTZ DEFAULT now(),
    last_verified     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_country  ON scholarships(country);
CREATE INDEX idx_levels   ON scholarships USING GIN(degree_levels);
CREATE INDEX idx_fields   ON scholarships USING GIN(fields);
CREATE INDEX idx_funding  ON scholarships(funding_type);
CREATE INDEX idx_deadline ON scholarships(deadline);
CREATE INDEX idx_open     ON scholarships(is_open);
```

**Normalization dictionaries** (kept in code, so filters are consistent):
- `fields`: controlled vocabulary — e.g. `computer_science, data_science, engineering,
  business, medicine, law, arts, social_sciences, natural_sciences, ...`. The AI is told to
  map each scholarship's field to one or more of these tags. This is what makes
  "show all Computer field courses" work reliably.
- `funding_type`: only `fully_funded | partial | unknown`.
- `country`/`region`: fixed list.

---

## 5. Source List (v1 targets)

Curated, trusted, high-yield. Start with the **bolded** ones (well-structured, high volume),
add the rest incrementally.

**Germany + Europe**
- **DAAD scholarship database** (Germany — largest single source)
- **EURAXESS** (fellowships across Europe)
- Erasmus Mundus Joint Masters catalogue (EU, fully funded)
- Study.eu / Scholars4Dev (Europe-wide aggregators)
- Selected government portals (e.g. Swedish Institute, Holland Scholarship, Eiffel/France)

**UK**
- **Chevening** (official, fully funded)
- Commonwealth Scholarships
- Selected top-university scholarship pages (Oxford, Edinburgh, etc.)

**Canada + Australia**
- Vanier Canada Graduate Scholarships
- Australia Awards / Research Training Program
- Selected university scholarship pages

**Cross-region aggregators (fill gaps)**
- ProFellow, IEFA, Scholars4Dev, Scholarship-Positions

> Each source becomes a small **config entry**: `{id, url(s), region, fetch_mode: rss|html|js,
> extraction_hint}`. Adding a source = adding one config entry, not new code.

**Rules:** respect `robots.txt`, add polite delays, never bypass logins/CAPTCHAs, prefer
RSS/official exports where available. Personal use + public pages = safe.

---

## 6. Pipeline (what runs each day)

```
1. LOAD source configs
2. For each source:
     a. Fetch listing page(s)         (httpx; Playwright only if JS-heavy)
     b. Find candidate detail links    (new/changed since last run)
     c. Fetch detail page text
3. For each candidate page:
     a. Send page text → free LLM with a strict JSON schema prompt
     b. Get back structured record (fields normalized to our vocabulary)
     c. Validate (dates parse? funding_type in allowed set?)
4. DEDUPE by fingerprint; UPSERT into Postgres
     - new  → insert (first_seen = now)
     - seen → update last_seen / refresh fields
5. HYGIENE: mark is_open=false where deadline < today
6. LOG run summary (counts: fetched, new, updated, expired, errors)
```

**AI extraction prompt contract** (the key to reliability): the model is given the page text
and told to return **only** JSON matching our schema, mapping field/funding/level to our
controlled vocabularies, using `null`/`unknown` when not stated (never guess deadlines).

**Rate-limit strategy:** batch pages, respect free-tier limits, rotate Gemini→Groq on 429,
process in small daily chunks (a personal DB doesn't need everything in one run).

---

## 7. Build Roadmap (phase by phase)

### Phase 0 — Foundation (accounts + skeleton)
- [ ] Create free accounts: **Neon** (DB), **Google AI Studio** (Gemini key), **Groq** (key), GitHub repo.
- [ ] Repo skeleton: `config/sources.yaml`, `src/`, `requirements.txt`, `.github/workflows/daily.yml`.
- [ ] Create the `scholarships` table + normalization dictionaries.
- [ ] Store keys as GitHub Secrets.
- **Done when:** `python -m src.healthcheck` connects to DB + calls the LLM successfully.

### Phase 1 — Vertical slice (prove the whole pipeline on ONE source)
- [ ] Implement fetch → extract → validate → upsert for **DAAD only**.
- [ ] Confirm real, correctly-structured rows land in Neon.
- **Done when:** I can run one SQL query and see clean DAAD scholarships with deadlines/funding/fields.

### Phase 2 — Add extraction robustness + more sources
- [ ] Generalize the fetcher/extractor to be config-driven.
- [ ] Add **Chevening**, **EURAXESS**, then the rest of the source list one at a time.
- [ ] Add JS-rendering (Playwright) only for sources that need it.
- **Done when:** 8–15 sources across all 3 regions populate the DB reliably.

### Phase 3 — Automation + hygiene
- [ ] Wire up **GitHub Actions daily cron**.
- [ ] Add expired-deadline flagging, dedupe hardening, run-summary logging.
- [ ] Add simple error handling so one broken source doesn't kill the run.
- **Done when:** it runs itself every day and stays fresh without me touching it.

### Phase 4 — Query convenience (bridge to frontend)
- [ ] A few saved SQL "views"/scripts: e.g. `open_fully_funded_cs_masters`, `deadline_within_30_days`.
- [ ] (Optional) export filtered results to CSV.
- **Done when:** I can answer "fully funded CS Masters in Europe, IELTS ≤ 6.5, open" in one command.

### Phase 5 (later) — Frontend
- Simple web UI on **Vercel/GitHub Pages** reading from Neon/Supabase, with dropdown filters
  (field, level, funding, IELTS, country). Design later.

---

## 8. Known Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Source site redesigns → scraper breaks | AI extraction adapts to layout; per-source isolation so one break ≠ total failure; run-summary flags sources returning 0 |
| Free LLM rate limits | Small daily batches, Gemini→Groq rotation, retry w/ backoff |
| AI hallucinated deadlines/fields | Strict "null if not stated, never guess dates" prompt + validation of parsed dates |
| Duplicates across aggregators | `fingerprint` unique hash + upsert |
| DB free-tier size | Text-only rows are tiny; 3 GB holds hundreds of thousands; prune expired periodically |
| Legal/ToS | Public pages only, respect robots.txt, polite delays, no login bypass, personal use |
| "Every field/everywhere" is infinite | Curated finite source list = covers the legitimate majority (how real aggregators work too) |

---

## 9. What I need from you to start building

1. Confirm this plan (or tweak scope/sources).
2. Then Phase 0: I'll set up the repo skeleton, DB schema, and tell you exactly which free
   accounts to create and where to paste the keys.

*Nothing here costs money. The only "cost" is occasional maintenance when a source site changes.*
