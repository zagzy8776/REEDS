from __future__ import annotations

from app.scraper import sportybet


SOCCER_PAYLOAD = {
    "data": {
        "tournaments": [
            {
                "name": "Premier League",
                "events": [
                    {
                        "id": "sr:match:123456",
                        "homeTeamName": "Arsenal",
                        "awayTeamName": "Chelsea",
                        "estimateStartTime": 1787338800000,
                        "markets": [
                            {
                                "id": "1",
                                "name": "1X2",
                                "outcomes": [
                                    {"id": "1", "name": "Home", "odds": "2.10"},
                                    {"id": "2", "name": "Away", "odds": "3.90"},
                                    {"id": "x", "name": "Draw", "odds": "3.40"},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
}


def test_parse_current_sportybet_shape():
    rows = sportybet._parse_payload(SOCCER_PAYLOAD, "soccer", 10)
    assert len(rows) == 1
    row = rows[0]
    assert row["sportybet_match_id"] == "sr:match:123456"
    assert row["home_team"] == "Arsenal"
    assert row["away_team"] == "Chelsea"
    assert row["league"] == "Premier League"
    assert row["home_odds"] == 2.10
    assert row["draw_odds"] == 3.40
    assert row["away_odds"] == 3.90


def test_parse_deduplicates_same_event():
    payload = {
        "events": [
            {
                "id": "sr:match:1",
                "homeTeamName": "A",
                "awayTeamName": "B",
                "homeOdds": "2.00",
                "drawOdds": "3.00",
                "awayOdds": "4.00",
            },
            {
                "id": "sr:match:1",
                "homeTeamName": "A",
                "awayTeamName": "B",
                "homeOdds": "2.01",
                "drawOdds": "3.01",
                "awayOdds": "4.01",
            },
        ]
    }
    rows = sportybet._parse_payload(payload, "soccer", 10)
    assert len(rows) == 1
    assert rows[0]["home_odds"] == 2.01


def test_missing_odds_does_not_create_fake_prices():
    payload = {
        "events": [
            {
                "id": "sr:match:2",
                "homeTeamName": "A",
                "awayTeamName": "B",
            }
        ]
    }
    rows = sportybet._parse_payload(payload, "soccer", 10)
    assert len(rows) == 1
    assert rows[0]["home_odds"] is None
    assert rows[0]["draw_odds"] is None
    assert rows[0]["away_odds"] is None


def test_malformed_payload_returns_empty():
    assert sportybet._parse_payload("not-json", "soccer", 10) == []


def test_http_403_returns_none(monkeypatch):
    class Response:
        status_code = 403

        def raise_for_status(self):
            raise AssertionError("403 should be handled before raise_for_status")

    monkeypatch.setattr(sportybet.requests, "get", lambda *args, **kwargs: Response())
    assert sportybet._request_json("https://example.test", {}, retries=0) is None


def test_http_404_returns_none(monkeypatch):
    class Response:
        status_code = 404

        def raise_for_status(self):
            raise AssertionError("404 should be handled before raise_for_status")

    monkeypatch.setattr(sportybet.requests, "get", lambda *args, **kwargs: Response())
    assert sportybet._request_json("https://example.test", {}, retries=0) is None


def test_invalid_odds_are_rejected():
    assert sportybet._to_odds(None) is None
    assert sportybet._to_odds("bad") is None
    assert sportybet._to_odds("0") is None
    assert sportybet._to_odds("2.38") == 2.38
