"""Unit tests for AfriScores season resolution / matching logic.

These cover the fix that pins ``season_id`` via table-name canonical matching:
``2025-2026`` resolves to the ``2025/2026`` table, and the following season
``2026/2027`` is correctly rejected. No network is required — the HTTP client
is faked so the GraphQL table listing is fully deterministic.

Run:  python -m pytest tests/test_afriscores_season_matching.py -q
"""
from app.scraper.afriscores_provider import AfriScoresProvider


class _FakeResp:
    """Stand-in for a requests.Response with a canned JSON body."""

    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeHttp:
    """Minimal stand-in for requests.Session returning a canned GraphQL payload."""

    def __init__(self, payload):
        self._payload = payload
        self.calls: list = []

    def post(self, *args, **kwargs) -> "_FakeResp":
        self.calls.append((args, kwargs))
        return _FakeResp(self._payload)

    def get(self, *args, **kwargs) -> "_FakeResp":
        self.calls.append((args, kwargs))
        return _FakeResp(self._payload)


def _make_provider(tables):
    """Build an AfriScoresProvider that returns ``tables`` from the table listing."""
    provider = AfriScoresProvider(http_client=_FakeHttp({"data": {"tables": tables}}))
    # Pre-seed the per-league table cache so _tables_for_league never hits the wire.
    provider._league_cache["tables:8"] = tables
    return provider


# EPL's AfriScores league_id is "8". Two seasons are present to prove that
# requesting 2025-2026 picks the 2025/2026 row and never the 2026/2027 row.
EPL_TABLES = [
    {"id": "t1", "name": "2025/2026", "season_id": "42", "league_id": "8"},
    {"id": "t2", "name": "2026/2027", "season_id": "99", "league_id": "8"},
]


def test_canon_season_normalises_labels():
    c = AfriScoresProvider
    assert c._canon_season("2025/2026") == "2025/2026"
    assert c._canon_season("2025-2026") == "2025/2026"
    assert c._canon_season("2025-26") == "2025/2026"
    assert c._canon_season("2025") == "2025/2026"
    assert c._canon_season("2526") is None  # ambiguous 4-digit code
    assert c._canon_season("2026/2027") == "2026/2027"
    assert c._canon_season("") is None
    assert c._canon_season("garbage") is None


def test_season_label_matches_accepts_equivalent_rejects_next():
    m = AfriScoresProvider._season_label_matches
    assert m("2025/2026", "2025-2026") is True
    assert m("2025/2026", "2025/2026") is True
    # '2025-2026' must NOT match the following season '2026/2027'
    assert m("2025/2026", "2026/2027") is False
    assert m("2026/2027", "2025-2026") is False


def test_season_matches_rejects_wrong_season_table():
    provider = _make_provider(EPL_TABLES)

    # The '2025/2026' table matches label '2025-2026'
    assert provider._season_matches(EPL_TABLES[0], "2025-2026") is True
    # The SAME table must NOT match the next season label
    assert provider._season_matches(EPL_TABLES[0], "2026/2027") is False
    # And the '2026/2027' table must not match '2025-2026'
    assert provider._season_matches(EPL_TABLES[1], "2025-2026") is False

    # No season requested -> always matches (fall back to fetch-all)
    assert provider._season_matches(EPL_TABLES[0], "") is True

    # season_id exact match still works even when the table name is empty
    assert provider._season_matches({"id": "t3", "name": "", "season_id": "42"}, "42") is True


def test_season_id_for_label_resolves_canonical_match():
    provider = _make_provider(EPL_TABLES)

    # '8' is EPL's AfriScores league id; '2025-2026' -> season_id of 2025/2026 table
    sid = provider._season_id_for_label("8", "2025-2026")
    assert sid == "42"
    # Must NOT pick the 2026/2027 row when asked for 2025-2026
    assert sid != "99"

    # Asking for the next season resolves its own (different) row
    assert provider._season_id_for_label("8", "2026/2027") == "99"

    # A season that does not exist returns None
    assert provider._season_id_for_label("8", "2024/2025") is None
    # None season -> None (no pinning)
    assert provider._season_id_for_label("8", None) is None
