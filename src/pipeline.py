"""The daily pipeline: fetch -> extract -> upsert -> hygiene.

Run:  python -m src.pipeline            (all enabled sources)
      python -m src.pipeline daad       (only the given source id)
"""
from __future__ import annotations

import sys

from . import config, db, discover, extract, fetch
from .sources import load_sources


def _prioritise(session, src: dict, candidates: list) -> list:
    """Order candidates so a limited daily budget eventually covers them all.

    MAX_PAGES_PER_RUN is smaller than some sources' catalogues (DAAD alone has
    ~100 in-scope programmes). Taking them in feed order every day would refresh
    the same first N forever and never reach the tail, so: never-seen pages first,
    then least-recently-refreshed.
    """
    try:
        index = session.run(db.last_seen_index, src["id"])
    except Exception as e:  # noqa: BLE001 - ordering is an optimisation, not critical
        print(f"  [priority] falling back to feed order: {e}")
        return candidates

    keyed = []
    fresh = 0
    for cand in candidates:
        ext = cand.seed.get("external_id")
        fp = db.fingerprint({"source_id": src["id"], "external_id": ext}) if ext else None
        seen = index.get(fp) if fp else index.get(cand.url)
        if seen is None:
            fresh += 1
        # Group 0 (never seen) sorts ahead of group 1; within group 1, oldest first.
        # The two groups never compare their second element against each other.
        keyed.append(((0, 0) if seen is None else (1, seen.timestamp()), cand))

    keyed.sort(key=lambda pair: pair[0])
    if len(candidates) > config.MAX_PAGES_PER_RUN:
        print(f"  {fresh} never seen before; oldest-first for the rest "
              f"(budget {config.MAX_PAGES_PER_RUN}/run)")
    return [cand for _, cand in keyed]


def dead_sources(per_source: dict[str, dict[str, int]]) -> list[str]:
    """Sources that stored nothing this run.

    A site redesign or a moved feed shows up as zero rows, not as an exception, so
    the run totals still look healthy. These are called out by name instead — it is
    the only warning that a source has silently stopped working.
    """
    return [
        sid for sid, t in per_source.items()
        if t["new"] == 0 and t["updated"] == 0
    ]


def _new_tally() -> dict[str, int]:
    return {"candidates": 0, "fetched": 0, "new": 0, "updated": 0, "errors": 0}


def run(only_source_id: str | None = None) -> dict:
    sources = load_sources(only_enabled=True)
    if only_source_id:
        sources = [s for s in sources if s["id"] == only_source_id]
        if not sources:
            print(f"No enabled source with id '{only_source_id}'. "
                  f"Check config/sources.yaml (is enabled: true?).")
            return {}

    stats = {"fetched": 0, "new": 0, "updated": 0, "expired": 0, "errors": 0}
    # Per-source tallies: a source that quietly returns nothing is invisible in the
    # totals, so each one is tracked and reported separately.
    per_source: dict[str, dict[str, int]] = {}
    budget = config.MAX_PAGES_PER_RUN

    # A Session, not a bare connection: this loop idles for minutes on HTTP and the
    # LLM, and Neon closes idle connections. See db.Session.
    session = db.Session()
    try:
        run_id = session.run(db.start_run)

        for src in sources:
            print(f"\n=== Source: {src['name']} ({src['id']}) ===")
            tally = per_source.setdefault(src["id"], _new_tally())

            try:
                candidates = discover.discover(src)
            except Exception as e:  # noqa: BLE001 - one broken source must not stop the run
                print(f"  [discover error] {src['id']}: {e}")
                stats["errors"] += 1
                tally["errors"] += 1
                continue

            tally["candidates"] = len(candidates)
            if not candidates:
                print("  no candidates found for this source.")
                stats["errors"] += 1
                tally["errors"] += 1
            else:
                candidates = _prioritise(session, src, candidates)

            for cand in candidates:
                url = cand.url
                if budget <= 0:
                    print("  [budget] MAX_PAGES_PER_RUN reached; stopping early.")
                    break
                budget -= 1
                stats["fetched"] += 1
                tally["fetched"] += 1

                html = fetch.get_html(url, src.get("fetch_mode", "html"))
                if not html:
                    stats["errors"] += 1
                    tally["errors"] += 1
                    continue

                content = fetch.page_text(html)
                if len(content) < 200:  # too thin to be a real listing
                    continue

                try:
                    rec = extract.extract(content, src, url, seed=cand.seed)
                except Exception as e:  # noqa: BLE001 - one bad page shouldn't kill the run
                    print(f"  [extract error] {url}: {e}")
                    stats["errors"] += 1
                    tally["errors"] += 1
                    continue

                if not rec:
                    continue

                try:
                    result = session.run(db.upsert_scholarship, rec)
                    stats[result] += 1
                    tally[result] += 1
                    flag = "NEW " if result == "new" else "upd "
                    print(f"  {flag}{rec['title'][:70]}")
                except Exception as e:  # noqa: BLE001
                    print(f"  [db error] {url}: {e}")
                    stats["errors"] += 1
                    tally["errors"] += 1

            if budget <= 0:
                break

        stats["expired"] = session.run(db.flag_expired)
        notes = only_source_id or "all"
        dead = dead_sources(per_source)
        if dead:
            notes += f" | no records from: {','.join(dead)}"
        session.run(db.finish_run, run_id, stats, notes=notes)
    finally:
        session.close()

    print("\n--- Run summary ---")
    for k, v in stats.items():
        print(f"  {k:8}: {v}")

    if per_source:
        print("\n--- Per source ---")
        for sid, t in per_source.items():
            print(f"  {sid:22} candidates={t['candidates']:4} fetched={t['fetched']:4} "
                  f"new={t['new']:4} updated={t['updated']:4} errors={t['errors']:4}")
    for sid in dead_sources(per_source):
        print(f"  !! {sid} stored NO records this run — check the source config")

    return {**stats, "per_source": per_source}


if __name__ == "__main__":
    config.enable_utf8_output()
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    run(arg)
