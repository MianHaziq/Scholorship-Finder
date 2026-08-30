"""Environment + settings loading."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(name: str, default: str | None = None, required: bool = False) -> str | None:
    # Treat an EMPTY variable as absent. GitHub Actions sets every mapped secret,
    # so an unset optional secret arrives as "" — and os.getenv would then return
    # that empty string instead of the default, leaving the model name blank.
    val = os.getenv(name) or default
    if required and not val:
        raise RuntimeError(
            f"Missing required env var {name}. Copy .env.example to .env and fill it in."
        )
    return val


DATABASE_URL = _get("DATABASE_URL")
GEMINI_API_KEY = _get("GEMINI_API_KEY")
# Working defaults, so the daily run succeeds even when the optional model
# secrets are not set. Aliases, never pinned versions: both previous defaults
# (gemini-2.0-flash, llama-3.3-70b-versatile) were retired and 404'd every call.
GEMINI_MODEL = _get("GEMINI_MODEL", "gemini-flash-lite-latest")
GROQ_API_KEY = _get("GROQ_API_KEY")
GROQ_MODEL = _get("GROQ_MODEL", "openai/gpt-oss-120b")

FETCH_DELAY_SECONDS = float(_get("FETCH_DELAY_SECONDS", "2"))
MAX_PAGES_PER_RUN = int(_get("MAX_PAGES_PER_RUN", "40"))

SOURCES_FILE = ROOT / "config" / "sources.yaml"
SCHEMA_FILE = ROOT / "schema.sql"


def enable_utf8_output() -> None:
    """Make stdout/stderr survive non-ASCII on a Windows console.

    The default Windows codepage is cp1252. Printing an accented scholarship name
    ("Universitat Hamburg"), an em dash, or the healthcheck's tick raised
    UnicodeEncodeError and killed the command outright — mid-listing, so the output
    looked truncated rather than failed. Every entry point calls this first.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):  # piped, redirected, or already fine
            pass
