"""Bounded, lazy model bundle cache for the small Render runtime.

Render's free instance has 512 MiB RAM. Deserializing a large multi-estimator
joblib bundle on every prediction run (or worse, during upload validation) is
what caused repeated OOM kills. This module centralizes artifact loading so:

  * nothing is deserialized at startup or during upload validation;
  * a bundle is loaded only when inference actually needs it;
  * at most ``MAX_CACHED_BUNDLES`` bundles are resident at once;
  * the cache is invalidated when the file changes (mtime/size).

``load_model_bundle`` returns ``None`` for missing/unreadable artifacts so the
prediction engines can fall back to their heuristic paths instead of crashing.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Any

import joblib

log = logging.getLogger(__name__)

MAX_CACHED_BUNDLES = 2
_lock = threading.Lock()
_cache: "OrderedDict[str, tuple[tuple[int, int], Any]]" = OrderedDict()


def _cache_key(path: str, mtime: int, size: int) -> str:
    return f"{path}|{mtime}|{size}"


def model_bundle_filesize(path: str) -> int:
    """Return the on-disk artifact size in MiB (0 when missing)."""
    try:
        return os.path.getsize(path) // (1024 * 1024)
    except OSError:
        return 0


def load_model_bundle(path: str | None) -> Any | None:
    """Load a joblib bundle through the bounded cache, or ``None`` on failure.

    The cache is keyed on path + mtime + size so a replaced artifact is picked
    up automatically without manual invalidation. Only the most recently used
    bundles stay resident, keeping memory bounded on the free instance.
    """
    if not path:
        return None
    try:
        stat = os.stat(path)
        mtime = int(stat.st_mtime)
        size = int(stat.st_size)
    except OSError:
        return None
    if size <= 0:
        return None

    key = _cache_key(path, mtime, size)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached

    try:
        bundle = joblib.load(path)
    except Exception:
        log.exception("Model bundle failed to load: %s", path)
        return None

    with _lock:
        _cache[key] = bundle
        _cache.move_to_end(key)
        while len(_cache) > MAX_CACHED_BUNDLES:
            _cache.popitem(last=False)
    return bundle


def clear_model_cache() -> None:
    """Drop all resident bundles. Used after uploads/syncs to free memory."""
    with _lock:
        _cache.clear()
    log.info("Model bundle cache cleared")


def cached_bundle_count() -> int:
    with _lock:
        return len(_cache)