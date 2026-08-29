"""Polite HTTP fetching + link/text extraction.

Respects robots.txt, adds a delay between requests, sends a real User-Agent.
JS-heavy sources (fetch_mode: js) are handled by Playwright if installed.
"""
from __future__ import annotations

import time
import urllib.robotparser as robotparser
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from . import config

USER_AGENT = (
    "ScholarshipFinderBot/1.0 (personal, non-commercial; respects robots.txt)"
)

_robots_cache: dict[str, robotparser.RobotFileParser] = {}


def _allowed(url: str) -> bool:
    """Check robots.txt for this URL's host (cached). Fail-open on fetch errors."""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    rp = _robots_cache.get(base)
    if rp is None:
        rp = robotparser.RobotFileParser()
        rp.set_url(urljoin(base, "/robots.txt"))
        try:
            rp.read()
        except Exception:  # noqa: BLE001 - if robots is unreachable, don't hard-block
            rp = None
        _robots_cache[base] = rp
    if rp is None:
        return True
    return rp.can_fetch(USER_AGENT, url)


def get_html(url: str, mode: str = "html") -> str | None:
    """Fetch a page's HTML. Returns None if disallowed or on error."""
    if not _allowed(url):
        print(f"  [robots] skipped (disallowed): {url}")
        return None

    time.sleep(config.FETCH_DELAY_SECONDS)  # be polite

    if mode == "js":
        return _get_html_js(url)

    # One retry: transient DNS/connection blips ("getaddrinfo failed") otherwise
    # silently drop pages from the run. HTTP status errors are NOT retried — a 404
    # is a real answer and retrying it just wastes a request.
    for attempt in (1, 2):
        try:
            with httpx.Client(
                headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.text
        except httpx.HTTPStatusError as e:
            print(f"  [fetch error] {url}: {e}")
            return None
        except Exception as e:  # noqa: BLE001 - transport/DNS problem; worth one retry
            if attempt == 1:
                time.sleep(3)
                continue
            print(f"  [fetch error] {url}: {e}")
            return None
    return None


def _get_html_js(url: str) -> str | None:
    """Render with Playwright (only used when a source sets fetch_mode: js)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [js] Playwright not installed; run: pip install playwright && playwright install chromium")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="networkidle", timeout=45000)
            html = page.content()
            browser.close()
            return html
    except Exception as e:  # noqa: BLE001
        print(f"  [js fetch error] {url}: {e}")
        return None


def find_detail_links(listing_html: str, base_url: str, link_filter: str | None) -> list[str]:
    """Collect candidate scholarship detail URLs from a listing page."""
    tree = HTMLParser(listing_html)
    links: set[str] = set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href", "")
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        full = urljoin(base_url, href)
        if link_filter and link_filter not in full:
            continue
        links.add(full)
    return sorted(links)


def page_text(html: str, max_chars: int = 20000) -> str:
    """Strip a page down to readable text for the LLM (drops scripts/styles/nav noise)."""
    tree = HTMLParser(html)
    for tag in tree.css("script, style, noscript, svg, header, footer, nav"):
        tag.decompose()
    body = tree.body or tree.root
    text = body.text(separator="\n", strip=True) if body else ""
    # Collapse excessive blank lines.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)[:max_chars]
