"""Redis-backed cache with graceful in-process degradation.

Redis is used only for read caching, short-lived context, provider responses,
and locks — never as the relational source of truth (PostgreSQL remains that).
When ``REDIS_URL`` is unset or unreachable the module falls back to a small
in-process TTL store so the app never fails at startup just because Redis is
missing.

Guidelines enforced here:
  * Redis keys are namespaced (``reeds:c:``).
  * Every key is written with a TTL (bounded memory).
  * ``cache_get_or_set`` delegates to the producer only once per TTL.
  * Redis failures are caught and fall back to the local store.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

_NAMESPACE = "reeds:c:"
_MAX_TTL = 3600
_lock = threading.Lock()
_fill_lock = threading.Lock()
_client = None
_available: bool | None = None

# In-process fallback store: key -> (expires_at, json_string)
_inproc: dict[str, tuple[float, str]] = {}


def _connect() -> Any | None:
    """Lazily build the Redis client; ``None`` when Redis must be skipped."""
    global _client, _available
    if _available is False:
        return None
    if _client is not None:
        return _client
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        _available = False
        log.info("REDIS_URL not set; using in-process TTL cache")
        return None
    try:
        import redis as redis_module
        client = redis_module.Redis.from_url(
            url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True
        )
        client.ping()
    except Exception:
        _available = False
        log.warning("Redis unavailable; falling back to in-process TTL cache")
        return None
    _client = client
    _available = True
    log.info("Redis cache connected")
    return client


def _inproc_set(full_key: str, raw: str, ttl: int) -> None:
    with _lock:
        _inproc[full_key] = (time.time() + max(1, min(ttl, _MAX_TTL)), raw)
        if len(_inproc) > 2048:
            now = time.time()
            for stale in [k for k, v in _inproc.items() if v[0] < now]:
                _inproc.pop(stale, None)


def _inproc_get(full: str) -> str | None:
    with _lock:
        entry = _inproc.get(full)
        if entry is None:
            return None
        if entry[0] < time.time():
            _inproc.pop(full, None)
            return None
        return entry[1]


def _get_value(full: str) -> str | None:
    client = _connect()
    if client is None:
        return _inproc_get(full)
    try:
        return client.get(full)
    except Exception:
        log.warning("Redis get failed; falling back to in-process cache")
        return _inproc_get(full)


def _set_value(full: str, raw: str, ttl: int) -> None:
    ttl = max(1, min(int(ttl), _MAX_TTL))
    client = _connect()
    if client is None:
        _inproc_set(full, raw, ttl)
        return
    try:
        client.set(full, raw, ex=ttl)
    except Exception:
        log.warning("Redis set failed; using in-process cache")
        _inproc_set(full, raw, ttl)


def cache_key(*parts: Any) -> str:
    """Namespaced cache key from arbitrary parts."""
    return _NAMESPACE + ":".join(str(p) for p in parts)


def cache_get(key: str) -> Any | None:
    """Return the cached JSON-decoded value or None."""
    raw = _get_value(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    """Cache a JSON-serializable value with a mandatory TTL."""
    try:
        _set_value(key, json.dumps(value, default=str), ttl)
    except Exception:
        log.debug("cache_set skipped for %s (not serializable)", key)


def cache_delete(key: str) -> None:
    client = _connect()
    if client is None:
        with _lock:
            _inproc.pop(key, None)
        return
    try:
        client.delete(key)
    except Exception:
        with _lock:
            _inproc.pop(key, None)


def cache_get_or_set(key: str, ttl: int, producer: Callable[[], Any]) -> Any:
    """Return the cached value, or compute it once via ``producer``.

    Uses a dedicated fill lock so concurrent callers do not stampede the same
    expensive producer. The producer runs *outside* the storage lock, and the
    fill lock is distinct from the in-process store lock (a plain ``Lock``)
    because cache reads take that store lock — reusing it here would
    self-deadlock.
    """
    cached = cache_get(key)
    if cached is not None:
        return cached
    with _fill_lock:
        cached = cache_get(key)
        if cached is not None:
            return cached
        value = producer()
    cache_set(key, value, ttl)
    return value