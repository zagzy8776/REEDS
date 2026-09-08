"""Tests for the Redis cache layer (graceful in-process fallback).

Redis is optional: with REDIS_URL unset the module must degrade to a bounded
in-process TTL store without failing. All keys are namespaced and every write
carries a TTL.
"""

import time

import pytest

from app.services import redis_cache as rc


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    rc._client = None
    rc._available = None
    rc._inproc.clear()
    yield
    rc._client = None
    rc._available = None
    rc._inproc.clear()


def test_keys_are_namespaced():
    key = rc.cache_key("fixtures", "status", "v1")
    assert key.startswith("reeds:c:")
    assert "fixtures" in key


def test_get_or_set_computes_producer_once():
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return {"value": calls["n"]}

    key = rc.cache_key("test", "once")
    assert rc.cache_get_or_set(key, 60, producer) == {"value": 1}
    assert rc.cache_get_or_set(key, 60, producer) == {"value": 1}
    assert rc.cache_get_or_set(key, 60, producer) == {"value": 1}
    assert calls["n"] == 1


def test_set_get_roundtrip_nested_payload():
    key = rc.cache_key("test", "roundtrip")
    payload = {"a": [1, 2, 3], "b": {"c": "d"}, "n": 1.5}
    rc.cache_set(key, payload, ttl=60)
    assert rc.cache_get(key) == payload


def test_cache_delete_removes_entry():
    key = rc.cache_key("test", "delete")
    rc.cache_set(key, {"x": 1}, ttl=60)
    assert rc.cache_get(key) == {"x": 1}
    rc.cache_delete(key)
    assert rc.cache_get(key) is None


def test_ttl_expiry_is_enforced():
    key = rc.cache_key("test", "ttl")
    rc.cache_set(key, {"x": 1}, ttl=1)
    assert rc.cache_get(key) == {"x": 1}
    time.sleep(1.1)
    assert rc.cache_get(key) is None


def test_redis_failure_falls_back_to_inproc():
    """If the Redis client errors mid-flight, reads fall back gracefully."""

    class ExplodingClient:
        def get(self, key):
            raise ConnectionError("redis down")

        def set(self, key, value, ex=None):
            raise ConnectionError("redis down")

        def delete(self, key):
            raise ConnectionError("redis down")

    rc._client = ExplodingClient()
    rc._available = True

    key = rc.cache_key("test", "fallback")
    rc.cache_set(key, {"y": 2}, ttl=60)  # fails on Redis, lands in in-proc
    assert rc.cache_get(key) == {"y": 2}
    rc.cache_delete(key)
    assert rc.cache_get(key) is None


def test_redis_outage_on_read_returns_none_not_crash():
    class ExplodingClient:
        def get(self, key):
            raise ConnectionError("redis down")

    rc._client = ExplodingClient()
    rc._available = True
    assert rc.cache_get(rc.cache_key("test", "down")) is None


def test_unbounded_ttl_is_clamped():
    key = rc.cache_key("test", "clamp")
    rc.cache_set(key, {"x": 1}, ttl=10_000_000)
    entry = rc._inproc[key]
    assert entry[0] <= time.time() + rc._MAX_TTL + 1
