# src/jiuwenswarm_instrumentor/model_context.py
from __future__ import annotations
import json
import logging
import os
import urllib.request
from pathlib import Path

logger = logging.getLogger("jiuwenswarm_instrumentor")

_LITELLM_URL = os.getenv(
    "OTEL_MODEL_CONTEXT_JSON_URL",
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
)
_CACHE_DIR = Path.home() / ".jiuwenswarm" / ".otel"
_CACHE_FILE = _CACHE_DIR / "model_context.json"
_CACHE_MAX_AGE_S = 7 * 24 * 3600  # 7 days
_FETCH_TIMEOUT = 3.0

# Bundled fallback (~30 common models)
_BUNDLED_PATH = Path(__file__).parent / "model_context.json"

_mapping: dict[str, int] | None = None


def _load_bundled() -> dict[str, int]:
    try:
        with open(_BUNDLED_PATH, encoding="utf-8") as f:
            return {k: int(v) for k, v in json.load(f).items()}
    except Exception:
        return {}


def _load_cache() -> dict[str, int] | None:
    try:
        if not _CACHE_FILE.exists():
            return None
        age = time.time() - _CACHE_FILE.stat().st_mtime
        if age > _CACHE_MAX_AGE_S:
            return None  # stale
        with open(_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {k: int(v) for k, v in data.items()}
    except Exception:
        return None


def _fetch_litelllm() -> dict[str, int] | None:
    """Fetch litellm's model JSON, extract model → max_input_tokens. Fail-soft → None."""
    try:
        import urllib.request
        with urllib.request.urlopen(_LITELLM_URL, timeout=_FETCH_TIMEOUT) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        result = {}
        for name, info in raw.items():
            if isinstance(info, dict):
                mit = info.get("max_input_tokens")
                if mit:
                    result[name] = int(mit)
        return result if result else None
    except Exception:
        logger.debug("[instrumentor] model context sync failed", exc_info=True)
        return None


def _save_cache(mapping: dict[str, int]):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f)
    except Exception:
        pass


def sync_model_context():
    """Try to sync from litellm → cache. Fail-soft: bundled or cached fallback."""
    global _mapping
    try:
        cached = _load_cache()
        if cached is not None:
            _mapping = cached
            logger.debug("[instrumentor] model context: using cache (%d models)", len(cached))
            return
        fetched = _fetch_litelllm()
        if fetched is not None:
            bundled = _load_bundled()
            fetched.update(bundled)  # bundled as fallback for models not in litellm
            _mapping = fetched
            _save_cache(fetched)
            logger.info("[instrumentor] model context synced from litellm (%d models)", len(fetched))
            return
        # fetch failed → use bundled
        _mapping = _load_bundled()
        logger.debug("[instrumentor] model context: using bundled (%d models)", len(_mapping))
    except Exception:
        _mapping = _load_bundled()


def get_max_context(model_name: str) -> int | None:
    """Look up max input tokens for a model. Exact match, then longest-prefix match.
    Returns None if unknown (don't guess)."""
    if not model_name or _mapping is None:
        return None
    m = _mapping.get(model_name)
    if m is not None:
        return m
    # longest prefix match (e.g., "gpt-4o-2024-08-06" → "gpt-4o")
    best = None
    best_len = 0
    for key, val in _mapping.items():
        if model_name.startswith(key) and len(key) > best_len:
            best = val
            best_len = len(key)
    return best


import time  # used by _load_cache; imported here to keep top-level clean
