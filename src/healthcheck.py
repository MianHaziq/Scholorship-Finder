"""Verify the environment is wired up correctly.

Run:  python -m src.healthcheck
Checks: .env present, DB connects + schema applies, LLM answers.
"""
from __future__ import annotations

import sys

from . import config, db, llm


def main() -> int:
    config.enable_utf8_output()
    ok = True

    print("1) Environment variables")
    for name, val in [
        ("DATABASE_URL", config.DATABASE_URL),
        ("GEMINI_API_KEY", config.GEMINI_API_KEY),
        ("GROQ_API_KEY", config.GROQ_API_KEY),
    ]:
        status = "set" if val else "MISSING"
        print(f"   {name:16}: {status}")
    if not config.DATABASE_URL:
        print("   -> Set DATABASE_URL before continuing.")
        return 1
    if not (config.GEMINI_API_KEY or config.GROQ_API_KEY):
        print("   -> Set at least one of GEMINI_API_KEY / GROQ_API_KEY.")
        return 1

    print("2) Database: connect + apply schema")
    try:
        db.init_db()
        with db.connect() as conn:
            n = conn.execute("SELECT count(*) AS c FROM scholarships").fetchone()["c"]
        print(f"   OK — schema ready, {n} scholarships currently stored.")
    except Exception as e:  # noqa: BLE001
        print(f"   FAILED: {e}")
        ok = False

    print("3) LLM: connectivity")
    try:
        provider = llm.ping()
        print(f"   OK — responded via {provider}.")
    except Exception as e:  # noqa: BLE001
        print(f"   FAILED: {e}")
        ok = False

    print("\nRESULT:", "ALL GOOD ✅" if ok else "problems above ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
