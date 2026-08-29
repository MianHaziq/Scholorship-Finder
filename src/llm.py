"""Free-LLM extraction layer.

Primary: Google Gemini free tier (huge context, reads whole pages).
Fallback: Groq (fast) when Gemini rate-limits or errors.

Both are asked to return STRICT JSON. We parse and hand back a dict.
"""
from __future__ import annotations

import json
import re

from tenacity import retry, stop_after_attempt, wait_exponential

from . import config


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response (handles ```json fences)."""
    text = text.strip()
    # Strip code fences if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    # Otherwise grab the outermost braces.
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    return json.loads(text)


# --- Gemini ---------------------------------------------------------------
_gemini_model = None


def _gemini():
    global _gemini_model
    if _gemini_model is None:
        import google.generativeai as genai

        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not set")
        genai.configure(api_key=config.GEMINI_API_KEY)
        _gemini_model = genai.GenerativeModel(config.GEMINI_MODEL)
    return _gemini_model


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
def _call_gemini(prompt: str) -> str:
    resp = _gemini().generate_content(
        prompt,
        generation_config={"temperature": 0, "response_mime_type": "application/json"},
    )
    return resp.text


# --- Groq -----------------------------------------------------------------
_groq_client = None


def _groq():
    global _groq_client
    if _groq_client is None:
        from groq import Groq

        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY not set")
        _groq_client = Groq(api_key=config.GROQ_API_KEY)
    return _groq_client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
def _call_groq(prompt: str) -> str:
    resp = _groq().chat.completions.create(
        model=config.GROQ_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content


def complete_json(prompt: str) -> dict:
    """Try Gemini first, fall back to Groq. Returns a parsed dict or raises."""
    errors = []
    if config.GEMINI_API_KEY:
        try:
            return _extract_json(_call_gemini(prompt))
        except Exception as e:  # noqa: BLE001 - fall through to Groq
            errors.append(f"gemini: {e}")
    if config.GROQ_API_KEY:
        try:
            return _extract_json(_call_groq(prompt))
        except Exception as e:  # noqa: BLE001
            errors.append(f"groq: {e}")
    raise RuntimeError("All LLM providers failed: " + " | ".join(errors) if errors
                       else "No LLM API key configured (set GEMINI_API_KEY or GROQ_API_KEY).")


def ping() -> str:
    """Cheap connectivity check used by healthcheck. Returns which provider answered.

    Reports the primary provider's error even when the fallback succeeds — silently
    swallowing it hides real breakage (e.g. a retired model name) behind a green run
    that is quietly burning the fallback's smaller quota for every page.
    """
    prompt = 'Return this exact JSON and nothing else: {"ok": true}'
    gemini_error = None
    if config.GEMINI_API_KEY:
        try:
            _extract_json(_call_gemini(prompt))
            return "gemini"
        except Exception as e:  # noqa: BLE001
            gemini_error = e
            print(f"   [gemini unavailable] {str(e)[:200]}")
    if config.GROQ_API_KEY:
        _extract_json(_call_groq(prompt))
        return "groq (FALLBACK — gemini failed above)" if gemini_error else "groq"
    raise RuntimeError("No LLM API key configured.")
