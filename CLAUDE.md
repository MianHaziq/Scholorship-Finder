# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

A personal, 100%-free scholarship finder. It collects scholarships (Bachelors + Masters,
all fields) from a curated source list, uses a free LLM to turn messy web pages into clean
structured records, stores them in Neon Postgres, and lets the owner filter by
field / level / funding / IELTS / country.

- **Scope v1:** Germany + Europe, UK, Canada + Australia. **No USA** (too fragmented).
- **Output v1:** a queryable database. A web frontend is Phase 5, not now.
- **Single user.** No accounts, no auth, no multi-tenancy.
- **Hard constraint: everything must stay free.** No paid APIs, hosting, or models.
  If a task seems to need a paid service, stop and raise it rather than adding one.

Full design and roadmap: [PLAN.md](PLAN.md). User-facing setup: [README.md](README.md).

## Environment — read this before running anything

**Use Python 3.12.** The machine has both 3.14 (PATH default) and 3.12.10.
`selectolax`, `PyYAML` and `grpcio` publish **no 3.14 wheels**, so pip falls back to
compiling C source and fails with *"Microsoft Visual C++ 14.0 required"*. 3.12 was
installed side-by-side with `winget install Python.Python.3.12` specifically to avoid this.

The venv is already built on 3.12. Always invoke it explicitly — do **not** rely on a bare
`python`, which resolves to 3.14:

```powershell
.venv\Scripts\python.exe -m src.healthcheck
.venv\Scripts\python.exe -m src.pipeline daad
.venv\Scripts\python.exe -m src.query --open
```

If the venv ever needs rebuilding: `py -3.12 -m venv .venv` (never `python -m venv`).
CI ([.github/workflows/daily.yml](.github/workflows/daily.yml)) pins 3.12 to match.

Shell is **PowerShell**. `&&`, `||`, ternaries and null-coalescing are unavailable
(Windows PowerShell 5.1). Chain with `;` or `if ($?) { ... }`.

## Architecture

```
config/sources.yaml   curated source list — adding a source is a CONFIG entry, not code
schema.sql            tables + indexes (idempotent; applied by db.init_db())
src/
  config.py           loads .env
  vocab.py            controlled vocabularies (fields, levels, funding, country->region)
  sources.py          loads source configs
  fetch.py            polite HTTP: robots.txt, delay, real UA; HTML -> text
  discover.py         finds candidate pages: link scraping OR the site's JSON data feed
  llm.py              free AI: Gemini primary, Groq fallback, strict JSON
  extract.py          page text (+ feed seed) -> validated record
  db.py               upsert, dedupe fingerprint, expiry hygiene, run log
  pipeline.py         daily orchestrator: discover -> prioritise -> fetch -> extract -> upsert
  query.py            CLI filtering / CSV export / SAVED_QUERIES presets
  export_site.py      renders the whole DB into one self-contained HTML page
  healthcheck.py      verify env + DB + LLM
```

Data flows: **discover → prioritise → fetch → extract (LLM) → validate → upsert → hygiene**.

### Source discovery: prefer the site's own data feed

`discover.py` has three modes, set per-source in `sources.yaml`:

- `links` (default) — scrape `<a href>` from the listing page.
- `static` — a hand-listed set of URLs, for a source that is ONE programme rather
  than a database (Chevening, Holland Scholarship, Eiffel). Scraping such a site
  returns only navigation links. Entries may carry per-URL seeds (`country`,
  `degree_levels`, `funding_type`) for facts config knows better than the page does.
- `json_feed` — read the structured feed the site's own frontend consumes.

**When a source returns 0 candidate links, its listing is rendered client-side.** Before
reaching for Playwright, view-source and scan its `<script src=...>` list for a data file.
DAAD does exactly this: its listing HTML contains zero detail links, while its entire
catalogue ships as static TaffyDB files under
`www2.daad.de/bundles/daadstipendiendatenbanklsh/data/a/js/` (`scholarships.js`,
`deadlines.js`, plus `subjectgroups`/`status`/`origin` lookups). Detail pages are plainly
server-rendered at `?detail={sapProgid}`.

`json_feed` is preferred over scraping wherever a feed exists, because it:
1. works when the listing is a JS app (scraping silently collects nothing),
2. supplies clean title/subject/level fields the LLM then **cannot get wrong**,
3. filters out-of-scope records *before* spending LLM calls (DAAD: 149 → 103), and
4. yields a permanent id used as the dedupe fingerprint.

Config keys: `feed_url`, `unwrap` (`taffy`|`assign`|none), `detail_url` template,
`seed` (our_field ← feed_field), `map` (feed codes → our vocab, with `all_means`),
`require_any` (scope filter), `records_path`.

**Seed only fields the feed states authoritatively.** Do not seed a field that merely
duplicates another — DAAD's `programmnameEn` is just a copy of the title, so `provider`
is deliberately left to the LLM, which reads the real organisation from the detail page's
Contact block.

### Seed / LLM merge rules (extract.py)

- `title`, `provider` — seed wins over the LLM when present.
- `degree_levels`, `fields` — **union** of seed and LLM. The feed is the reliable
  baseline; the LLM adds detail the taxonomy lacks (DAAD files computer science under
  "Mathematics and Natural Sciences", so `computer_science` can only come from page text).
- `summary` — LLM preferred; feed intro is a fallback only (feed intros are truncated
  mid-sentence).
- Everything else (funding, IELTS, deadline, eligibility) — LLM only.
- A seeded record came from the source's own scholarship feed, so `is_scholarship: false`
  from the LLM is ignored for seeded records.

### LLM providers

Gemini is primary, Groq is the fallback ([src/llm.py](src/llm.py)). Both providers
**retire model names**, which is a silent, total pipeline failure — `gemini-2.0-flash`
and `llama-3.3-70b-versatile` were both dead on first real use. Use `-latest` aliases,
never pinned versions. To see what a key can actually call:

```powershell
curl -H "x-goog-api-key: $env:GEMINI_API_KEY" https://generativelanguage.googleapis.com/v1beta/models
curl -H "Authorization: Bearer $env:GROQ_API_KEY" https://api.groq.com/openai/v1/models
```

**Use the LITE model.** On free tiers latency varies enormously within one family —
measured on a real 9,677-char extraction prompt:

| model | latency |
|---|---|
| `gemini-flash-latest` | **296.2s** — unusable |
| `gemini-flash-lite-latest` | **1.7s** — current primary |
| `openai/gpt-oss-120b` (Groq) | 2.6s — current fallback |

Output quality is equivalent here because a strict JSON schema constrains it. Before
debugging a "stuck" pipeline, benchmark the model: the first DAAD run looked frozen at
11 rows and was simply waiting ~5 minutes per page.

`llm.ping()` must keep reporting the primary's error even when the fallback answers.
It used to swallow it, so the healthcheck blamed Groq for a Gemini fault and would have
shown green while quietly burning the fallback's smaller quota on every page.

### Never invent dates — do not regress this

`extract._parse_deadline()` parses **only** the model's strict `deadline_iso` field and
ignores `deadline_raw` prose entirely. Fuzzy-parsing prose invented deadlines: dateutil
read *"deadlines differ and **may** be requested"* as May, took the day from today's
date, and produced a real-looking deadline for a scholarship that has none — which
`flag_expired()` then marked closed, hiding it from `--open` queries. A missing deadline
must stay NULL. Model-supplied links go through `extract._normalize_url()`, since pages
often give a bare host ("daad.de/go/...") that `urljoin` would otherwise mangle.

### Neon drops idle connections — do not regress this

**Never hold one `psycopg` connection across a whole pipeline run.** Neon is serverless:
it closes idle connections and scales computes to zero, and this loop idles for minutes
at a time waiting on HTTP and the LLM. When that happened, every remaining page failed
with `the connection is closed` and the run crashed in `flag_expired` — one blip cost
~85 of 103 pages.

All pipeline DB work therefore goes through `db.Session`, which reconnects and retries
once on `psycopg.OperationalError`: `session.run(db.upsert_scholarship, rec)`, not
`db.upsert_scholarship(conn, rec)`. `db.connect()` is still fine for short one-shot
scripts (healthcheck, query).

`fetch.get_html()` likewise retries once on transport/DNS errors (a real
`getaddrinfo failed` blip dropped 5 pages), but deliberately does **not** retry HTTP
status errors — a 404 is a real answer.

### Dedupe — do not regress this

`db.fingerprint(rec)` uses `source_id:external_id`, falling back to
`title|provider|deadline_raw` only when there is no id. That fallback is entirely
AI-derived text, so any rewording between daily runs would **insert a duplicate
instead of updating** — observed for real: one Commonwealth title came back with a
curly apostrophe on the second run.

`discover()` therefore guarantees every candidate has an identity: a feed id where
one exists (DAAD's `sapProgid`), otherwise **the detail URL**. Do not remove that
`setdefault` — two Commonwealth pages both extract to the title "Commonwealth
Fellowships", and without it they collapse into one row, losing a programme.

### Budget rotation — do not regress this

`MAX_PAGES_PER_RUN` (40) is smaller than some catalogues (DAAD alone has ~103 in-scope
programmes). `pipeline._prioritise()` orders candidates **never-seen first, then
least-recently-refreshed**. Without it, a fixed feed order would refresh the same first
40 forever and never reach the tail. Verified: full DAAD coverage in 3 runs
(40 → 80 → 103).

## Conventions

- **Adding a source = editing `config/sources.yaml`**, not writing code. If a source
  genuinely cannot be expressed in config, extend the config vocabulary in
  `discover.py` rather than special-casing a source id.
- **One broken source must never kill the run.** Per-source and per-page work is wrapped
  in `try/except`; errors increment `stats["errors"]` and the loop continues.
- **Never guess data.** The extraction prompt mandates `null`/`unknown` when a page
  doesn't state something, and explicitly forbids inventing deadlines or scores.
- **Controlled vocabularies are the point.** All `fields` / `degree_levels` /
  `funding_type` values must come from `src/vocab.py`; free text belongs in `field_raw`
  or `deadline_raw`. This is what makes Phase-2 filtering reliable.
- **Politeness is non-negotiable:** respect robots.txt, keep `FETCH_DELAY_SECONDS`,
  send a real User-Agent, never bypass logins or CAPTCHAs. Public pages only.
- Test a new source with `python -m src.pipeline <id>` before setting `enabled: true`.

## Testing against the real DB

The Neon database is the owner's real data. To rehearse pipeline changes without
persisting anything, open a **non-autocommit** connection and roll it back
(`db.connect()` is autocommit, so construct `psycopg.connect(...)` directly), and stub
`llm.complete_json` with a canned dict to avoid burning free-tier quota. Put scratch
scripts in the session scratchpad, not in the repo.

## Secrets

`.env` holds `DATABASE_URL`, `GEMINI_API_KEY`, `GROQ_API_KEY` and is gitignored — never
commit it, never paste key values into terminal output, code, or docs. In CI these come
from GitHub Actions Secrets. `.env.example` is the committed template and must stay
free of real values.

## Status

- **Phase 0 (foundation): done.** Neon live (PostgreSQL 18.6); `scholarships`
  (25 cols, 8 indexes) + `run_log` applied. Venv on 3.12 with all deps as wheels.
- **Phase 1 (DAAD vertical slice): in progress.** Discovery, extract-merge, upsert,
  dedupe and query all verified end-to-end with a stubbed LLM in a rolled-back
  transaction. Real LLM keys are now in `.env`; first live collection is the current step.
- **Phase 2 (more sources): in progress.** Enabled and verified live: `daad` (102),
  `chevening`, `commonwealth` (6), `holland_scholarship`, `eiffel`.
  Probed and left disabled **with the reason recorded in `sources.yaml`** —
  `euraxess` (HTTP 403 to our UA), `australia_awards` (**robots.txt disallows —
  must not be enabled**), `vanier` (TLS chain failure, and PhD-only so out of scope).
- **Phase 3 (automation): done.** Daily cron runs pytest before the pipeline.
  `pipeline.dead_sources()` names any source that stored nothing — a site redesign
  shows up as zero rows, not an exception, so the totals alone look healthy.
- **Phase 4 (query convenience): done.** `query.SAVED_QUERIES` presets +
  `--saved` / `--list-saved`, plus CSV export.
- **Phase 5 (frontend): done.** `python -m src.export_site` -> `site/index.html`.

### The frontend must not hold a DB credential

`export_site.py` embeds the rows as JSON and filters client-side. Do **not** "improve"
this by having the page query Neon directly — that publishes `DATABASE_URL` to every
viewer. A static file also keeps hosting free (GitHub Pages) and works offline.
`--fragment` emits body-only markup for publishing as an Artifact; the default emits a
complete document. Country names go through `vocab.canonical_country()` before storage,
or "UK" and "United Kingdom" appear as two separate entries in every filter.

## Tests

`.venv\Scripts\python.exe -m pytest` — 153 tests, ~1s, no network/DB/LLM (the feed,
fetch and `llm.complete_json` are all stubbed). They exist because every bug in the
"do not regress this" sections above reached the real database once. Each such
section has a matching test; verified they fail against the old implementations.
