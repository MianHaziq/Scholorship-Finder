"""Turn a scholarship page into a clean, validated record using a free LLM."""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin

from dateutil import parser as dateparser

from . import llm
from .vocab import DEGREE_LEVELS, FIELDS, FUNDING_TYPES, canonical_country, region_for

_PROMPT = """You are extracting structured data about ONE scholarship from a web page.

Return ONLY a JSON object with EXACTLY these keys:
{{
  "is_scholarship": boolean,   // false if this page is not actually a single scholarship listing
  "title": string,
  "provider": string|null,     // organisation offering it (e.g. "DAAD", university name)
  "country": string|null,      // WHERE THE STUDY/RESEARCH TAKES PLACE, normalized (e.g. "Germany","UK","Canada","Australia"; "Europe" for multi-country EU programmes)
  "degree_levels": string[],   // subset of {levels}
  "fields": string[],          // subset of {fields}  (map the subject to these tags; use "any_field" if open to all)
  "field_raw": string|null,    // the original subject/field text as written
  "funding_type": string,      // one of {funding}
  "funding_details": string|null, // what is covered (tuition, stipend, travel, etc.)
  "ielts_required": boolean|null, // true/false if stated, null if not mentioned
  "ielts_min": number|null,    // e.g. 6.5 if a specific IELTS score is stated, else null
  "other_language": string|null,  // other language requirement (TOEFL score, German level, etc.)
  "deadline_raw": string|null, // the deadline exactly as written on the page
  "deadline_iso": string|null, // that deadline as YYYY-MM-DD, ONLY if unambiguous; else null
  "apply_url": string|null,    // application / more-info link if present
  "summary": string|null,      // 1-2 sentence plain summary
  "eligibility": string|null   // short eligibility note
}}

RULES:
- "country" is the DESTINATION where the funded study/research happens, NOT the
  applicant's home/origin country. Many programmes are aimed at applicants FROM a
  particular country but fund study in the provider's country — in that case report
  the destination. Only report a different country if the study itself happens there.
- Use null / "unknown" when the page does not state something. NEVER guess a deadline or invent a score.
- funding_type: "fully_funded" only if the page clearly covers tuition + living/stipend;
  "partial" if it covers only some costs; otherwise "unknown".
- degree_levels and fields MUST only use the allowed values listed above.
- Output valid JSON only. No prose, no markdown.

SOURCE HINT: {hint}

PAGE CONTENT:
{content}
"""


def _clean_list(values, allowed: list[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    return [v for v in values if v in allowed]


def _parse_deadline(iso: str | None, raw: str | None) -> date | None:
    """Parse ONLY the model's strict ISO date field. Never read prose.

    `raw` is deliberately ignored. Fuzzy-parsing it invents dates: dateutil read
    "Application deadlines differ and MAY be requested..." as May and filled the
    day from today, producing a real-looking deadline for a scholarship that has
    none — which then got flagged expired and disappeared from --open queries.
    A missing deadline must stay NULL (the plan's "never guess a deadline" rule).
    """
    if not iso:
        return None
    try:
        dt = dateparser.parse(iso.strip(), fuzzy=False)
    except (ValueError, OverflowError, TypeError):
        return None
    if dt is None:
        return None
    # Guard against a model echoing prose into deadline_iso.
    return dt.date() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso.strip()) else None


def _normalize_url(candidate: str | None, base: str) -> str | None:
    """Make a model-supplied link absolute.

    Pages often show a bare host ("daad.de/go/en/stipa10000092"); urljoin would
    graft that onto the source path, so scheme-less hosts get https:// instead.
    """
    if not candidate:
        return None
    url = candidate.strip()
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return urljoin(base, url)
    # "example.com/path" — a host, not a path relative to the current page.
    # A dot alone isn't enough to tell them apart: "apply.html" is a filename.
    first = url.split("/", 1)[0].split("?", 1)[0]
    is_filename = re.search(r"\.(html?|php|aspx?|jsp|pdf|docx?|s?html)$", first, re.I)
    if "." in first and " " not in first and not is_filename:
        return "https://" + url
    return urljoin(base, url)


def _merge_tags(seeded: list[str] | None, from_llm: list[str], allowed: list[str]) -> list[str]:
    """Union of feed-supplied tags and AI-supplied tags, order preserved.

    The feed's tags are the reliable baseline (they come from the source's own
    taxonomy); the AI can add finer detail the taxonomy lacks — e.g. DAAD files
    computer science under "Mathematics and Natural Sciences", so the page text is
    the only place "computer_science" can come from.
    """
    out = [t for t in (seeded or []) if t in allowed]
    for t in from_llm:
        if t not in out:
            out.append(t)
    return out


def extract(
    page_content: str, source: dict, source_url: str, seed: dict | None = None
) -> dict | None:
    """Return a validated record dict, or None if the page isn't a real scholarship.

    `seed` carries fields already known for certain from a structured feed (see
    discover.py). Seeded title/provider override the AI; seeded tags merge with it.
    """
    seed = seed or {}
    prompt = _PROMPT.format(
        levels=DEGREE_LEVELS,
        fields=FIELDS,
        funding=FUNDING_TYPES,
        hint=source.get("hint", ""),
        content=page_content,
    )
    data = llm.complete_json(prompt)

    title = (seed.get("title") or data.get("title") or "").strip()
    if not title:
        return None
    # A seeded record came from the source's own scholarship feed, so we already
    # know it is one — don't let a thin page talk the AI out of it.
    if not seed and not data.get("is_scholarship"):
        return None

    funding = seed.get("funding_type") or data.get("funding_type")
    if funding not in FUNDING_TYPES:
        funding = "unknown"

    deadline = _parse_deadline(data.get("deadline_iso"), data.get("deadline_raw"))
    # A hand-listed source states its destination in config; trust that over the page,
    # which often talks mostly about the applicant's home country.
    country = canonical_country(seed.get("country") or data.get("country"))

    ielts_min = data.get("ielts_min")
    try:
        ielts_min = float(ielts_min) if ielts_min is not None else None
    except (ValueError, TypeError):
        ielts_min = None

    return {
        "source_id": source["id"],
        "source_url": source_url,
        "external_id": seed.get("external_id"),  # stable dedupe key when the feed has one
        "title": title,
        "provider": (seed.get("provider") or data.get("provider") or "").strip() or None,
        "country": country,
        # Only inherit the source's region when the country is unknown. Deriving it
        # from a KNOWN but unmapped country would label a Japan/Jordan programme
        # "Europe" just because the source is a European one, hiding the error.
        "region": region_for(country) if country else source.get("region"),
        "degree_levels": _merge_tags(
            seed.get("degree_levels"),
            _clean_list(data.get("degree_levels"), DEGREE_LEVELS),
            DEGREE_LEVELS,
        ),
        "fields": _merge_tags(
            seed.get("fields"), _clean_list(data.get("fields"), FIELDS), FIELDS
        ),
        "field_raw": data.get("field_raw"),
        "funding_type": funding,
        "funding_details": data.get("funding_details"),
        "ielts_required": data.get("ielts_required"),
        "ielts_min": ielts_min,
        "other_language": data.get("other_language"),
        "deadline": deadline,
        "deadline_raw": data.get("deadline_raw"),
        "is_open": (deadline is None) or (deadline >= date.today()),
        "apply_url": _normalize_url(data.get("apply_url"), source_url) or source_url,
        # Feed intros are truncated mid-sentence, so they're only a fallback.
        "summary": data.get("summary") or seed.get("summary"),
        "eligibility": data.get("eligibility"),
    }
