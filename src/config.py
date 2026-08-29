"""Environment + settings loading."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(
            f"Missing required env var {name}. Copy .env.example to .env and fill it in."
        )
    return val


DATABASE_URL = _get("DATABASE_URL")
GEMINI_API_KEY = _get("GEMINI_API_KEY")
GEMINI_MODEL = _get("GEMINI_MODEL", "gemini-2.0-flash")
GROQ_API_KEY = _get("GROQ_API_KEY")
GROQ_MODEL = _get("GROQ_MODEL", "llama-3.3-70b-versatile")

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
