"""Find candidate scholarship pages for a source.

Three discovery modes, chosen per-source in config/sources.yaml:

  links      (default) — fetch the listing page(s) and scrape <a href> detail links.
                         Works when the listing is server-rendered HTML.

  static               — a fixed list of known pages. For sources that are ONE
                         programme (Chevening, Holland Scholarship, Eiffel) rather
                         than a searchable database, so there is no listing to scrape.

  json_feed            — fetch a structured data feed the site's own frontend uses,
                         and build detail URLs from it. Needed when the listing page
                         is rendered client-side (scraping its HTML yields 0 links),
                         and better even when it isn't: the feed hands us clean
                         fields (title, subject, level) that we no longer have to
                         ask the LLM to infer.

All modes return the same thing: a list of Candidate(url, seed), where `seed` holds
fields already known for certain from the feed. The extractor treats those as
authoritative and only asks the LLM for what's genuinely missing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import fetch

# `var scholarships = TAFFY([...]);` — the client-side-DB wrapper DAAD ships its
# data in. We want the bare JSON array inside it.
_TAFFY_RE = re.compile(r"TAFFY\(\s*(\[.*\])\s*\)\s*;?\s*$", re.S)
# Generic `var x = [...]` / `x = {...}` assignment wrapper.
_ASSIGN_RE = re.compile(r"^\s*(?:var\s+)?\w+\s*=\s*([\[{].*[\]}])\s*;?\s*$", re.S)


@dataclass
class Candidate:
    url: str
    seed: dict[str, Any] = field(default_factory=dict)


def discover(src: dict) -> list[Candidate]:
    mode = (src.get("discovery") or {}).get("mode", "links")
    if mode == "json_feed":
        cands = _from_json_feed(src)
    elif mode == "static":
        cands = _from_static(src)
    else:
        cands = _from_links(src)

    # Every candidate needs a stable identity for db.fingerprint(). A feed supplies
    # a real id (DAAD's sapProgid); otherwise the detail URL is the stable thing.
    # Without this, link-mode records hash AI-written title/provider/deadline text:
    # two Commonwealth pages both extracted to the title "Commonwealth Fellowships",
    # which would collapse two distinct programmes into one row.
    for c in cands:
        c.seed.setdefault("external_id", c.url)
    return cands


# --------------------------------------------------------------------------- static


def _from_static(src: dict) -> list[Candidate]:
    """A hand-listed set of pages, each already a single scholarship.

    Some sources are one programme, not a database: Chevening has no per-scholarship
    detail pages to discover, so scraping its site yields only navigation links.
    Seeds may be supplied per URL for anything the page states poorly.
    """
    cfg = src["discovery"]
    out: list[Candidate] = []
    seen: set[str] = set()
    for entry in cfg.get("urls", []):
        if isinstance(entry, str):
            url, seed = entry, {}
        else:
            entry = dict(entry)
            url = entry.pop("url")
            seed = entry
        if url in seen:
            continue
        seen.add(url)
        out.append(Candidate(url=url, seed=seed))
    print(f"  static list -> {len(out)} page(s)")
    return out


# --------------------------------------------------------------------------- links


def _from_links(src: dict) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[str] = set()
    for listing_url in src.get("listing_urls", []):
        html = fetch.get_html(listing_url, src.get("fetch_mode", "html"))
        if not html:
            continue
        found = fetch.find_detail_links(html, listing_url, src.get("link_filter"))
        print(f"  listing {listing_url} -> {len(found)} candidate links")
        for u in found:
            if u not in seen:
                seen.add(u)
                out.append(Candidate(url=u))
    return out


# ----------------------------------------------------------------------- json feed


def _unwrap(text: str, style: str | None) -> Any:
    """Pull the JSON payload out of a .js file that assigns it to a variable."""
    text = text.strip()
    if style == "taffy":
        m = _TAFFY_RE.search(text)
    elif style == "assign":
        m = _ASSIGN_RE.match(text)
    else:
        return json.loads(text)
    if not m:
        raise ValueError(f"could not unwrap feed using style={style!r}")
    return json.loads(m.group(1))


def _dig(rec: dict, path: str) -> Any:
    """Read `a.b.c` out of nested dicts; returns None if any hop is missing."""
    cur: Any = rec
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _apply_map(rec: dict, spec: dict) -> list[str]:
    """Translate a feed's own codes into our controlled vocabulary.

    spec = {from: <field>, values: {<code>: <tag or [tags]>}}
    The source field may be a scalar or a list; result is de-duplicated, order kept.
    """
    raw = _dig(rec, spec["from"])
    if raw is None:
        return []
    codes = raw if isinstance(raw, list) else [raw]
    table = spec.get("values", {})
    out: list[str] = []
    for code in codes:
        tags = table.get(code, table.get(str(code)))
        if tags is None:
            continue
        for t in [tags] if isinstance(tags, str) else tags:
            if t not in out:
                out.append(t)
    # A record carrying every possible code is effectively "open to all".
    all_of = spec.get("all_means")
    if all_of and table and len(set(map(str, codes))) >= len(table):
        return [all_of]
    return out


def _passes(rec: dict, require_any: dict) -> bool:
    """Keep only records in scope (e.g. DAAD status 1/3 = Bachelors/Masters)."""
    for fieldname, wanted in require_any.items():
        raw = _dig(rec, fieldname)
        have = raw if isinstance(raw, list) else [raw]
        if not set(map(str, have)) & set(map(str, wanted)):
            return False
    return True


def _from_json_feed(src: dict) -> list[Candidate]:
    cfg = src["discovery"]
    url = cfg["feed_url"]

    try:
        resp = httpx.get(
            url, headers={"User-Agent": fetch.USER_AGENT}, timeout=60,
            follow_redirects=True,
        )
        resp.raise_for_status()
        records = _unwrap(resp.text, cfg.get("unwrap"))
    except Exception as e:  # noqa: BLE001 - a dead feed shouldn't kill the whole run
        print(f"  [feed error] {url}: {e}")
        return []

    if isinstance(records, dict):  # feed wrapped its list in an object
        records = _dig(records, cfg["records_path"]) if cfg.get("records_path") else []
    print(f"  feed {url} -> {len(records)} records")

    require_any = cfg.get("require_any") or {}
    seed_fields = cfg.get("seed") or {}
    maps = cfg.get("map") or {}
    template = cfg["detail_url"]
    # Hand-maintained escape hatch for feed entries that are not scholarships at
    # all. Needed because neither the feed nor the LLM flags them: DAAD's
    # "Important Information" portal stub reads as a real programme to the model.
    excluded = {str(x) for x in (cfg.get("exclude_ids") or [])}
    id_field = (seed_fields or {}).get("external_id")

    out: list[Candidate] = []
    seen: set[str] = set()
    skipped = 0

    for rec in records:
        if require_any and not _passes(rec, require_any):
            skipped += 1
            continue
        if excluded and id_field and str(_dig(rec, id_field)) in excluded:
            print(f"  excluded feed entry {_dig(rec, id_field)} (configured exclude_ids)")
            continue
        try:
            detail_url = template.format(**rec)
        except (KeyError, IndexError):
            continue
        if detail_url in seen:
            continue
        seen.add(detail_url)

        seed: dict[str, Any] = {}
        for our_name, feed_path in seed_fields.items():
            val = _dig(rec, feed_path)
            if isinstance(val, str):
                val = val.strip() or None
            if val:
                seed[our_name] = val
        for our_name, spec in maps.items():
            tags = _apply_map(rec, spec)
            if tags:
                seed[our_name] = tags

        out.append(Candidate(url=detail_url, seed=seed))

    if skipped:
        print(f"  {skipped} records skipped (out of scope), {len(out)} in scope")
    return out
