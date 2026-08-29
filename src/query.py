"""Command-line browsing of the database.

The web page (`python -m src.export_site`) covers the same ground visually; this
stays the quickest way to answer one question from a terminal, and the only way
to export a filtered CSV.

Examples:
  python -m src.query                          # latest 20 open scholarships
  python -m src.query --field computer_science --funding fully_funded --open
  python -m src.query --country Germany --level masters --ielts-max 6.5
  python -m src.query --deadline-days 30       # deadlines within 30 days
  python -m src.query --csv out.csv            # export current filter to CSV
  python -m src.query --saved cs_masters       # a named, ready-made search
  python -m src.query --list-saved             # show the named searches
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta

from . import config, db


# Ready-made searches (PLAN phase 4). Each is just a set of defaults for the same
# filters, so anything here can still be overridden on the command line.
SAVED_QUERIES: dict[str, dict] = {
    "cs_masters": {
        "help": "Fully funded CS Masters that are still open",
        "field": "computer_science", "level": "masters",
        "funding": "fully_funded", "open": True,
    },
    "closing_soon": {
        "help": "Anything open with a deadline in the next 30 days",
        "deadline_days": 30, "open": True,
    },
    "fully_funded_masters": {
        "help": "Every fully funded Masters still open",
        "level": "masters", "funding": "fully_funded", "open": True,
    },
    "bachelors": {
        "help": "Open Bachelors-level scholarships",
        "level": "bachelors", "open": True,
    },
    "no_ielts": {
        "help": "Open scholarships that do not state an IELTS requirement",
        "ielts_max": 0, "open": True,
    },
    "germany": {
        "help": "Open scholarships to study in Germany",
        "country": "Germany", "open": True,
    },
    "uk": {
        "help": "Open scholarships to study in the UK",
        "country": "UK", "open": True,
    },
}


def apply_saved(args) -> None:
    """Fill unset options from a saved search.

    Only values the user did not give are filled, so
    `--saved cs_masters --country Germany` narrows the saved search rather than
    fighting it.
    """
    preset = SAVED_QUERIES.get(args.saved)
    if preset is None:
        raise SystemExit(
            f"Unknown saved search {args.saved!r}. "
            f"Available: {', '.join(sorted(SAVED_QUERIES))}"
        )
    for key, value in preset.items():
        if key == "help":
            continue
        current = getattr(args, key, None)
        if current in (None, False):
            setattr(args, key, value)


def build_query(args) -> tuple[str, list]:
    where = []
    params: list = []

    if args.open:
        where.append("is_open = TRUE")
    if args.field:
        where.append("%s = ANY(fields)")
        params.append(args.field)
    if args.level:
        where.append("%s = ANY(degree_levels)")
        params.append(args.level)
    if args.funding:
        where.append("funding_type = %s")
        params.append(args.funding)
    if args.country:
        where.append("country = %s")
        params.append(args.country)
    if args.region:
        where.append("region = %s")
        params.append(args.region)
    if args.ielts_max is not None:
        # include rows with no IELTS requirement OR a requirement at/below the cap
        where.append("(ielts_min IS NULL OR ielts_min <= %s)")
        params.append(args.ielts_max)
    if args.deadline_days is not None:
        where.append("deadline IS NOT NULL AND deadline BETWEEN %s AND %s")
        params.extend([date.today(), date.today() + timedelta(days=args.deadline_days)])

    sql = "SELECT title, provider, country, degree_levels, fields, funding_type, " \
          "ielts_min, deadline, apply_url FROM scholarships"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (deadline IS NULL), deadline ASC, last_seen DESC"
    sql += f" LIMIT {int(args.limit)}"
    return sql, params


def main() -> None:
    config.enable_utf8_output()
    ap = argparse.ArgumentParser(description="Browse stored scholarships.")
    ap.add_argument("--saved", help="use a named search (see --list-saved)")
    ap.add_argument("--list-saved", action="store_true",
                    help="list the named searches and exit")
    ap.add_argument("--field")
    ap.add_argument("--level", choices=["bachelors", "masters", "phd"])
    ap.add_argument("--funding", choices=["fully_funded", "partial", "unknown"])
    ap.add_argument("--country")
    ap.add_argument("--region")
    ap.add_argument("--ielts-max", type=float, dest="ielts_max")
    ap.add_argument("--deadline-days", type=int, dest="deadline_days")
    ap.add_argument("--open", action="store_true", help="only currently open scholarships")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--csv", help="write results to this CSV file instead of printing")
    args = ap.parse_args()

    if args.list_saved:
        print("Saved searches (use with --saved <name>):\n")
        for name, preset in sorted(SAVED_QUERIES.items()):
            print(f"  {name:22} {preset['help']}")
        return
    if args.saved:
        apply_saved(args)

    sql, params = build_query(args)
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} rows to {args.csv}")
        return

    if not rows:
        print("No matching scholarships. (Have you run the pipeline yet?)")
        return

    for r in rows:
        dl = r["deadline"].isoformat() if r["deadline"] else "rolling/unknown"
        print(f"\n• {r['title']}")
        print(f"   {r['provider'] or '-'} | {r['country'] or '-'} | "
              f"{r['funding_type']} | levels={r['degree_levels']} | fields={r['fields']}")
        print(f"   IELTS≥{r['ielts_min'] if r['ielts_min'] is not None else '-'} | "
              f"deadline: {dl}")
        print(f"   {r['apply_url']}")
    print(f"\n{len(rows)} result(s).")


if __name__ == "__main__":
    main()
