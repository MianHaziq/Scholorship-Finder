# Scholarship Finder

A personal, continuously-updated scholarship database. 100% free stack.
Discovers scholarships from a curated list of trusted sources, uses a free AI model to
turn messy pages into clean structured records, stores them in a free Postgres database,
and lets you filter by field / level / funding / IELTS / country.

See [PLAN.md](PLAN.md) for the full design and roadmap.

---

## What you need (all free, no credit card)

| # | Account | Get the value | Paste into `.env` as |
|---|---------|---------------|----------------------|
| 1 | **Neon** (Postgres) | https://neon.tech → new project → *Connection string* (pooled) | `DATABASE_URL` |
| 2 | **Google AI Studio** (Gemini) | https://aistudio.google.com/app/apikey → *Create API key* | `GEMINI_API_KEY` |
| 3 | **Groq** (fallback AI) | https://console.groq.com/keys → *Create API Key* | `GROQ_API_KEY` |

You can start with just Neon + Gemini. Groq is an optional fallback for when Gemini rate-limits.

---

## Setup (one time)

> **Python version matters.** Use **3.12** (what the GitHub Actions workflow runs).
> On 3.14 several dependencies (`selectolax`, `PyYAML`, `grpcio`) have no prebuilt wheels
> yet, so pip tries to compile them from C source and fails with
> *"Microsoft Visual C++ 14.0 required"*. If you don't have 3.12:
> `winget install Python.Python.3.12` — it installs alongside your existing Python
> and doesn't change your default.

```bash
# 1. Create + activate a virtual environment (explicitly on 3.12)
py -3.12 -m venv .venv          # Windows
# python3.12 -m venv .venv      # macOS/Linux
.venv\Scripts\activate          # Windows PowerShell
# source .venv/bin/activate     # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your .env from the template, then fill in the 3 keys above
copy .env.example .env          # Windows
# cp .env.example .env          # macOS/Linux

# 4. Verify everything is wired up (creates the DB tables too)
python -m src.healthcheck
```

You want to see: `RESULT: ALL GOOD ✅`

---

## Daily use

```bash
# Collect scholarships from all ENABLED sources (see config/sources.yaml)
python -m src.pipeline

# Or run a single source while testing
python -m src.pipeline daad

# Browse what was collected
python -m src.query --open
python -m src.query --field computer_science --funding fully_funded --level masters --open
python -m src.query --country Germany --ielts-max 6.5
python -m src.query --deadline-days 30
python -m src.query --field computer_science --csv computer_scholarships.csv

# Ready-made searches
python -m src.query --list-saved
python -m src.query --saved cs_masters
python -m src.query --saved closing_soon
```

---

## Automate it (free, runs by itself)

The daily refresh runs on **GitHub Actions** — no server needed.

1. Push this project to a GitHub repo.
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**, and add:
   `DATABASE_URL`, `GEMINI_API_KEY`, `GROQ_API_KEY` (and optionally `GEMINI_MODEL`, `GROQ_MODEL`).
3. The workflow in [.github/workflows/daily.yml](.github/workflows/daily.yml) runs every day at
   06:00 UTC. You can also trigger it manually: repo → **Actions → Daily scholarship refresh → Run workflow**.

---

## Adding more sources

Edit [config/sources.yaml](config/sources.yaml) and flip a source to `enabled: true`
(or add a new entry). No code changes needed. Test it with
`python -m src.pipeline <id>` before enabling it in the daily run.

Three discovery modes are available (`discovery: mode:` in the config):

| mode | when to use |
|---|---|
| `links` (default) | the listing page is server-rendered HTML with real detail links |
| `static` | the source is ONE programme, not a database (Chevening, Eiffel) — list its URLs directly |
| `json_feed` | the listing is a JavaScript app, or the site ships a data feed |

**If a source finds 0 candidate links,** its listing page is almost certainly rendered
client-side. Before reaching for Playwright, view-source and look through its
`<script src=...>` list for a data file — many such sites ship their whole catalogue as
JSON. That's what DAAD does, so its config uses `discovery: mode: json_feed` to read the
catalogue in one request. This is preferred over scraping: the feed supplies clean
title/subject/level fields the AI can't then get wrong, lets us skip out-of-scope records
before spending LLM calls, and gives a permanent id used as the dedupe fingerprint.

---

## The web page (phase 5)

```bash
python -m src.export_site          # -> site/index.html
```

That writes ONE self-contained HTML file: every scholarship is embedded as JSON and
all filtering happens in your browser. Open it directly, or push the `site/` folder
and turn on GitHub Pages (Settings -> Pages -> deploy from branch).

It stays free and safe precisely because there is no server and no database
credential in the page — the obvious alternative, letting the page query Neon
directly, would put your connection string in front of anyone who views it.

Filters: search, status, degree level, funding, field, destination and an IELTS cap
(entries that state no IELTS score are kept, since an unstated requirement is not a
barrier). Rows carry a coloured stripe and a countdown chip for deadline urgency,
and the page follows your light/dark setting.

---

## Running the tests

```bash
.venv\Scripts\python.exe -m pytest
```

153 tests, about a second. They make no network, database or LLM calls — the data feed,
the fetcher and the model are all stubbed — so they are safe to run anytime and cost
nothing. They mainly lock down bugs that previously reached the real database:
invented deadlines, duplicate rows, unreachable catalogue pages, and dropped
connections.

---

## Sources currently enabled

| source | region | mode | notes |
|---|---|---|---|
| `daad` | Europe | `json_feed` | 102 in-scope programmes, the bulk of the data |
| `chevening` | UK | `static` | fully funded UK Masters |
| `commonwealth` | UK | `links` | 6 programmes |
| `holland_scholarship` | Europe | `static` | Netherlands |
| `eiffel` | Europe | `static` | France |

Three sources were probed and left **disabled**, with the reason recorded in
[config/sources.yaml](config/sources.yaml): EURAXESS returns HTTP 403 to our bot,
Vanier fails TLS and is PhD-only, and **Australia Awards' robots.txt disallows
crawling** — that one must stay off.

---

## Project layout

```
config/sources.yaml     # the curated source list (edit to add sources)
schema.sql              # database tables + indexes
src/
  config.py             # loads .env
  vocab.py              # controlled field/level/funding vocabularies
  sources.py            # loads source configs
  fetch.py              # polite HTTP + robots.txt + link/text extraction
  discover.py           # finds candidate pages: links / static list / JSON data feed
  llm.py                # free AI: Gemini primary, Groq fallback (strict JSON)
  extract.py            # page text -> validated scholarship record
  db.py                 # upsert, dedupe, expired-deadline hygiene, run log
  pipeline.py           # the daily orchestrator
  query.py              # CLI filtering / CSV export / saved searches
  export_site.py        # generates the static web page (phase 5)
  healthcheck.py        # verify env + DB + LLM
.github/workflows/daily.yml   # free daily cron
```
