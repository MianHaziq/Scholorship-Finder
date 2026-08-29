"""Load source configs from config/sources.yaml."""
from __future__ import annotations

import yaml

from . import config


def load_sources(only_enabled: bool = True) -> list[dict]:
    data = yaml.safe_load(config.SOURCES_FILE.read_text(encoding="utf-8"))
    sources = data.get("sources", [])
    if only_enabled:
        sources = [s for s in sources if s.get("enabled")]
    return sources
