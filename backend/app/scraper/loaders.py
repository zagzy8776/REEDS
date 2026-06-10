from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import Fixture
from app.scraper.api_clients import AllSportsApiClient, ApiBasketballClient, ApiFootballClient, ApiFootballComClient, FootballDataOrgClient, SportMonksFootballClient, TheOddsApiClient, TheSportsDbClient
from app.services.data_quality import resolve_team_name
from app.utils.team_names import normalize_team_name


FOOTBALL_DATA_MAP = {
    "Date": "match_date",
    "HomeTeam": "home_team",
    "AwayTeam": "away_team",
    "FTHG": "home_score",
    "FTAG": "away_score",
    "B365H": "home_odds",
    "B365D": "draw_odds",
    "B365A": "away_odds",
}


def read_csv_flexible(path: str, **kwargs) -> pd.DataFrame:
    """Read public sports CSVs that may use UTF-8 or legacy European encodings."""

    last_error: Exception | None = None
    for encoding in ("utf-8", "latin1", "cp1252"):
        try:
            return pd.read_csv(path, encoding=encoding, **kwargs)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return pd.read_csv(path, **kwargs)

BASKETBALL_DATA_MAP = {
    "Date": "match_date",
    "GAME_DATE": "match_date",
    "game_date": "match_date",
    "GAME_DATE_EST": "match_date",
    "date": "match_date",
    "HomeTeam": "home_team",
    "HOME_TEAM": "home_team",
    "HOME_TEAM_NAME": "home_team",
    "TEAM_NAME_home": "home_team",
    "home_team_name": "home_team",
    "home_team": "home_team",
    "VisitorTeam": "away_team",
    "AwayTeam": "away_team",
    "AWAY_TEAM": "away_team",
    "VISITOR_TEAM_NAME": "away_team",
    "TEAM_NAME_away": "away_team",
    "away_team_name": "away_team",
    "away_team": "away_team",
    "HomePTS": "home_score",
    "PTS_home": "home_score",
    "HOME_PTS": "home_score",
    "PTS_HOME": "home_score",
    "home_score": "home_score",
    "home_points": "home_score",
    "AwayPTS": "away_score",
    "PTS_away": "away_score",
    "AWAY_PTS": "away_score",
    "PTS_AWAY": "away_score",
    "away_score": "away_score",
    "away_points": "away_score",
}


def upsert_fixture(db: Session, fixture: Fixture) -> None:
    """Insert or update a fixture using the natural uniqueness key.

    SQLAlchemy merge only works by primary key. Our downloaded CSV rows do not know
    primary keys, so we manually find existing rows to avoid duplicate-key errors.
    """

    existing = (
        db.query(Fixture)
        .filter(
            Fixture.sport == fixture.sport,
            Fixture.league == fixture.league,
            Fixture.match_date == fixture.match_date,
            Fixture.home_team == fixture.home_team,
            Fixture.away_team == fixture.away_team,
        )
        .first()
    )
    if existing:
        existing.season = fixture.season
        existing.home_score = fixture.home_score
        existing.away_score = fixture.away_score
        existing.home_odds = fixture.home_odds
        existing.draw_odds = fixture.draw_odds
        existing.away_odds = fixture.away_odds
        existing.source = fixture.source
        existing.extra = fixture.extra
    else:
        db.add(fixture)


def _to_int_or_none(value) -> int | None:
    try:
        if value is None or pd.isna(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float_or_none(value) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


ALLSPORTS_SPORT_MAP = {
    "football": "soccer",
    "soccer": "soccer",
    "basketball": "basketball",
    "tennis": "tennis",
    "cricket": "cricket",
    "hockey": "hockey",
    "baseball": "baseball",
    "american-football": "american_football",
    "volleyball": "volleyball",
    "handball": "handball",
}


THESPORTSDB_SPORT_MAP = {
    "Soccer": "soccer",
    "Basketball": "basketball",
    "American Football": "american_football",
    "Cricket": "cricket",
    "Tennis": "tennis",
    "Ice Hockey": "hockey",
    "Baseball": "baseball",
    "Rugby": "rugby",
    "Motorsport": "motorsport",
    "Fighting": "mma",
}


def _first_present(row: dict, keys: list[str]):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_api_football_1x2_odds(payload: dict) -> dict[int, dict[str, float | None]]:
    odds_by_fixture: dict[int, dict[str, float | None]] = {}
    for item in payload.get("response", []) or []:
        fixture_id = item.get("fixture", {}).get("id")
        if not fixture_id:
            continue
        for bookmaker in item.get("bookmakers", []) or []:
            for bet in bookmaker.get("bets", []) or []:
                name = str(bet.get("name", "")).lower()
                if name not in {"match winner", "1x2", "winner"}:
                    continue
                row = {"home_odds": None, "draw_odds": None, "away_odds": None}
                for value in bet.get("values", []) or []:
                    label = str(value.get("value", "")).lower()
                    odd = _to_float_or_none(value.get("odd"))
                    if label in {"home", "1"}:
                        row["home_odds"] = odd
                    elif label in {"draw", "x"}:
                        row["draw_odds"] = odd
                    elif label in {"away", "2"}:
                        row["away_odds"] = odd
                odds_by_fixture[int(fixture_id)] = row
                break
            if int(fixture_id) in odds_by_fixture:
                break
    return odds_by_fixture


def _parse_the_odds_api_soccer_h2h(payloads: list[dict | list], target_dates: list[str]) -> dict[tuple[str, str, str], dict[str, float | None]]:
    odds_by_match: dict[tuple[str, str, str], dict[str, float | None]] = {}
    wanted_dates = set(target_dates)
    for payload in payloads:
        if isinstance(payload, dict):
            events = payload.get("response", []) or []
        else:
            events = payload or []
        for event in events:
            match_date = pd.to_datetime(event.get("commence_time"), errors="coerce")
            if pd.isna(match_date) or match_date.date().isoformat() not in wanted_dates:
                continue
            home = str(event.get("home_team") or "")
            away = str(event.get("away_team") or "")
            if not home or not away:
                continue
            row = {"home_odds": None, "draw_odds": None, "away_odds": None}
            for bookmaker in event.get("bookmakers", []) or []:
                for market in bookmaker.get("markets", []) or []:
                    if str(market.get("key", "")).lower() != "h2h":
                        continue
                    for outcome in market.get("outcomes", []) or []:
                        name = str(outcome.get("name") or "")
                        price = _to_float_or_none(outcome.get("price"))
                        if normalize_team_name(name, "soccer") == normalize_team_name(home, "soccer"):
                            row["home_odds"] = price
                        elif normalize_team_name(name, "soccer") == normalize_team_name(away, "soccer"):
                            row["away_odds"] = price
                        elif name.lower() == "draw":
                            row["draw_odds"] = price
                    if row["home_odds"] or row["draw_odds"] or row["away_odds"]:
                        key = (match_date.date().isoformat(), normalize_team_name(home, "soccer"), normalize_team_name(away, "soccer"))
                        odds_by_match[key] = row
                        break
                if row["home_odds"] or row["draw_odds"] or row["away_odds"]:
                    break
    return odds_by_match


def _fetch_the_odds_api_soccer_odds(api_key: str | None, sport_keys: list[str], target_dates: list[str]) -> dict[tuple[str, str, str], dict[str, float | None]]:
    if not api_key or not sport_keys:
        return {}
    client = TheOddsApiClient(api_key)
    payloads = []
    for sport_key in sport_keys:
        try:
            payloads.append(client.h2h_odds(sport_key))
        except Exception:  # noqa: BLE001 - odds are optional; fixtures should still ingest
            continue
    return _parse_the_odds_api_soccer_h2h(payloads, target_dates)


def ingest_api_football_fixtures(
    db: Session,
    api_key: str,
    target_dates: list[str],
    include_odds: bool = True,
    the_odds_api_key: str | None = None,
    the_odds_api_sport_keys: list[str] | None = None,
) -> int:
    client = ApiFootballClient(api_key)
    external_odds_by_match = _fetch_the_odds_api_soccer_odds(the_odds_api_key, the_odds_api_sport_keys or [], target_dates) if include_odds else {}
    count = 0
    for target_date in target_dates:
        fixture_payload = client.fixtures_by_date(target_date)
        odds_by_fixture = {}
        if include_odds and not external_odds_by_match:
            try:
                odds_by_fixture = _parse_api_football_1x2_odds(client.odds_by_date(target_date))
            except Exception:  # noqa: BLE001 - odds are optional; fixtures should still ingest
                odds_by_fixture = {}
        for item in fixture_payload.get("response", []) or []:
            fixture_data = item.get("fixture", {})
            league_data = item.get("league", {})
            teams = item.get("teams", {})
            goals = item.get("goals", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            fixture_id = fixture_data.get("id")
            match_date = pd.to_datetime(fixture_data.get("date"), errors="coerce")
            if pd.isna(match_date) or not home.get("name") or not away.get("name"):
                continue
            home_name = resolve_team_name(db, str(home.get("name")), "soccer", "api_football")
            away_name = resolve_team_name(db, str(away.get("name")), "soccer", "api_football")
            odds_key = (match_date.date().isoformat(), normalize_team_name(home_name, "soccer"), normalize_team_name(away_name, "soccer"))
            odds = external_odds_by_match.get(odds_key) or odds_by_fixture.get(int(fixture_id or 0), {})
            fx = Fixture(
                sport="soccer",
                league=league_data.get("name") or "Football",
                season=str(league_data.get("season") or match_date.year),
                match_date=match_date.date(),
                home_team=home_name,
                away_team=away_name,
                home_score=_to_int_or_none(goals.get("home")),
                away_score=_to_int_or_none(goals.get("away")),
                home_odds=odds.get("home_odds"),
                draw_odds=odds.get("draw_odds"),
                away_odds=odds.get("away_odds"),
                source="api_football",
                extra={"api_fixture_id": fixture_id, "status": fixture_data.get("status"), "country": league_data.get("country"), "odds_source": "the_odds_api" if odds_key in external_odds_by_match else "api_football" if odds else None},
            )
            upsert_fixture(db, fx)
            count += 1
        db.commit()
    return count


def _score_from_sportmonks_scores(scores: list, participant_id: int | None) -> int | None:
    for score in scores or []:
        if score.get("participant_id") == participant_id and str(score.get("description", "")).upper() in {"CURRENT", "FT", "FULLTIME"}:
            return _to_int_or_none((score.get("score") or {}).get("goals"))
    return None


def ingest_sportmonks_football_fixtures(db: Session, api_key: str, target_dates: list[str]) -> int:
    client = SportMonksFootballClient(api_key)
    count = 0
    for target_date in target_dates:
        payload = client.fixtures_by_date(target_date)
        for item in payload.get("data", []) or []:
            participants = item.get("participants", []) or []
            home = next((p for p in participants if (p.get("meta") or {}).get("location") == "home"), None)
            away = next((p for p in participants if (p.get("meta") or {}).get("location") == "away"), None)
            match_date = pd.to_datetime(item.get("starting_at"), errors="coerce")
            if pd.isna(match_date) or not home or not away:
                continue
            fx = Fixture(
                sport="soccer",
                league=(item.get("league") or {}).get("name") or "Football",
                season=str(item.get("season_id") or match_date.year),
                match_date=match_date.date(),
                home_team=resolve_team_name(db, str(home.get("name")), "soccer", "sportmonks"),
                away_team=resolve_team_name(db, str(away.get("name")), "soccer", "sportmonks"),
                home_score=_score_from_sportmonks_scores(item.get("scores") or [], home.get("id")),
                away_score=_score_from_sportmonks_scores(item.get("scores") or [], away.get("id")),
                source="sportmonks",
                extra={"sportmonks_fixture_id": item.get("id"), "state_id": item.get("state_id")},
            )
            upsert_fixture(db, fx)
            count += 1
        db.commit()
    return count


def ingest_football_data_org_matches(db: Session, api_key: str, target_dates: list[str]) -> int:
    client = FootballDataOrgClient(api_key)
    count = 0
    if not target_dates:
        return count
    payload = client.matches_by_range(target_dates[0], target_dates[-1])
    for item in payload.get("matches", []) or []:
            match_date = pd.to_datetime(item.get("utcDate"), errors="coerce")
            home = item.get("homeTeam") or {}
            away = item.get("awayTeam") or {}
            score = item.get("score") or {}
            full_time = score.get("fullTime") or {}
            if pd.isna(match_date) or not home.get("name") or not away.get("name"):
                continue
            comp = item.get("competition") or {}
            fx = Fixture(
                sport="soccer",
                league=comp.get("name") or "Football",
                season=str((item.get("season") or {}).get("startDate") or match_date.year)[:4],
                match_date=match_date.date(),
                home_team=resolve_team_name(db, str(home.get("name")), "soccer", "football_data_org"),
                away_team=resolve_team_name(db, str(away.get("name")), "soccer", "football_data_org"),
                home_score=_to_int_or_none(full_time.get("home")),
                away_score=_to_int_or_none(full_time.get("away")),
                source="football_data_org",
                extra={"football_data_match_id": item.get("id"), "status": item.get("status")},
            )
            upsert_fixture(db, fx)
            count += 1
    db.commit()
    return count


def ingest_apifootball_com_events(db: Session, api_key: str, target_dates: list[str]) -> int:
    client = ApiFootballComClient(api_key)
    count = 0
    if not target_dates:
        return count
    payload = client.fixtures_by_range(target_dates[0], target_dates[-1])
    events = payload if isinstance(payload, list) else payload.get("response", []) or payload.get("data", []) or []
    for item in events:
            match_date = pd.to_datetime(item.get("match_date") or item.get("event_date"), errors="coerce")
            home = item.get("match_hometeam_name") or item.get("home_team")
            away = item.get("match_awayteam_name") or item.get("away_team")
            if pd.isna(match_date) or not home or not away:
                continue
            fx = Fixture(
                sport="soccer",
                league=item.get("league_name") or item.get("country_name") or "Football",
                season=str(match_date.year),
                match_date=match_date.date(),
                home_team=resolve_team_name(db, str(home), "soccer", "apifootball_com"),
                away_team=resolve_team_name(db, str(away), "soccer", "apifootball_com"),
                home_score=_to_int_or_none(item.get("match_hometeam_score")),
                away_score=_to_int_or_none(item.get("match_awayteam_score")),
                source="apifootball_com",
                extra={"apifootball_match_id": item.get("match_id"), "status": item.get("match_status")},
            )
            upsert_fixture(db, fx)
            count += 1
    db.commit()
    return count


def ingest_api_basketball_games(db: Session, api_key: str, target_dates: list[str]) -> int:
    client = ApiBasketballClient(api_key)
    count = 0
    for target_date in target_dates:
        payload = client.games_by_date(target_date)
        for item in payload.get("response", []) or []:
            teams = item.get("teams", {})
            scores = item.get("scores", {})
            league = item.get("league", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            home_scores = scores.get("home", {}) if isinstance(scores.get("home"), dict) else {}
            away_scores = scores.get("away", {}) if isinstance(scores.get("away"), dict) else {}
            match_date = pd.to_datetime(item.get("date"), errors="coerce")
            if pd.isna(match_date) or not home.get("name") or not away.get("name"):
                continue
            fx = Fixture(
                sport="basketball",
                league=league.get("name") or "Basketball",
                season=str(item.get("season") or match_date.year),
                match_date=match_date.date(),
                home_team=resolve_team_name(db, str(home.get("name")), "basketball", "api_basketball"),
                away_team=resolve_team_name(db, str(away.get("name")), "basketball", "api_basketball"),
                home_score=_to_int_or_none(home_scores.get("total")),
                away_score=_to_int_or_none(away_scores.get("total")),
                source="api_basketball",
                extra={"api_game_id": item.get("id"), "status": item.get("status"), "country": league.get("country")},
            )
            upsert_fixture(db, fx)
            count += 1
        db.commit()
    return count


def ingest_allsportsapi_events(db: Session, api_key: str, target_dates: list[str], sports: list[str] | None = None) -> int:
    """Ingest fixtures from AllSportsAPI using one ranged request per sport.

    This is free-tier friendly: with football,basketball,tennis,cricket enabled
    and a 7-day window it uses only 4 calls per scheduler run, not 28+ calls.
    AllSportsAPI free accounts expose only two assigned leagues, but whatever
    your account can see will be normalized into the shared fixtures table.
    """

    if not target_dates:
        return 0
    client = AllSportsApiClient(api_key)
    count = 0
    for provider_sport in sports or ["football", "basketball", "tennis", "cricket", "hockey", "baseball", "american-football", "volleyball", "handball"]:
        canonical_sport = ALLSPORTS_SPORT_MAP.get(provider_sport, provider_sport.replace("-", "_"))
        try:
            payload = client.events_by_range(provider_sport, target_dates[0], target_dates[-1])
        except Exception:  # noqa: BLE001 - one provider sport should not stop others
            continue
        events = payload if isinstance(payload, list) else payload.get("result", []) or payload.get("response", []) or []
        for item in events:
            match_date = pd.to_datetime(_first_present(item, ["event_date", "match_date", "date", "event_time"]), errors="coerce")
            home = _first_present(item, ["event_home_team", "match_hometeam_name", "home_team", "homeTeam", "player1"])
            away = _first_present(item, ["event_away_team", "match_awayteam_name", "away_team", "awayTeam", "player2"])
            if pd.isna(match_date) or not home or not away:
                continue
            league = _first_present(item, ["league_name", "event_league", "country_name", "league", "tournament_name"]) or provider_sport.title()
            season = str(_first_present(item, ["league_season", "season", "event_season"]) or match_date.year)
            fx = Fixture(
                sport=canonical_sport,
                league=str(league)[:80],
                season=season[:20],
                match_date=match_date.date(),
                home_team=resolve_team_name(db, str(home), canonical_sport, "allsportsapi"),
                away_team=resolve_team_name(db, str(away), canonical_sport, "allsportsapi"),
                home_score=_to_int_or_none(_first_present(item, ["event_final_result_home", "event_home_final_result", "event_home_result", "match_hometeam_score", "home_score"])),
                away_score=_to_int_or_none(_first_present(item, ["event_final_result_away", "event_away_final_result", "event_away_result", "match_awayteam_score", "away_score"])),
                home_odds=_to_float_or_none(_first_present(item, ["odd_1", "home_odds"])),
                draw_odds=_to_float_or_none(_first_present(item, ["odd_x", "draw_odds"])),
                away_odds=_to_float_or_none(_first_present(item, ["odd_2", "away_odds"])),
                source="allsportsapi",
                extra={"provider_sport": provider_sport, "event_key": item.get("event_key") or item.get("match_id"), "raw_status": item.get("event_status") or item.get("match_status")},
            )
            upsert_fixture(db, fx)
            count += 1
        db.commit()
    return count


def ingest_thesportsdb_events(db: Session, api_key: str | None, target_dates: list[str], sports: list[str] | None = None, max_calls: int = 8) -> int:
    """Ingest TheSportsDB events with a hard request cap.

    The free tier is useful as coverage insurance, but we keep calls low by
    limiting date+sport combinations. Defaults favor breadth: many sports for
    today only, with a hard max_calls cap so free API quota is protected.
    """

    client = TheSportsDbClient(api_key or "3")
    provider_sports = sports or ["Soccer", "Basketball", "American Football", "Cricket", "Tennis", "Ice Hockey", "Baseball", "Rugby", "Motorsport", "Fighting"]
    count = 0
    calls = 0
    # Breadth before depth: one call per sport for the closest dates first.
    for target_date in target_dates[:1]:
        for provider_sport in provider_sports:
            if calls >= max_calls:
                return count
            calls += 1
            canonical_sport = THESPORTSDB_SPORT_MAP.get(provider_sport, provider_sport.lower().replace(" ", "_"))
            try:
                payload = client.events_day(target_date, provider_sport)
            except Exception:  # noqa: BLE001
                continue
            for item in payload.get("events", []) or []:
                match_date = pd.to_datetime(item.get("dateEvent") or target_date, errors="coerce")
                home = item.get("strHomeTeam")
                away = item.get("strAwayTeam")
                if pd.isna(match_date) or not home or not away:
                    continue
                fx = Fixture(
                    sport=canonical_sport,
                    league=(item.get("strLeague") or provider_sport)[:80],
                    season=str(item.get("strSeason") or match_date.year)[:20],
                    match_date=match_date.date(),
                    home_team=resolve_team_name(db, str(home), canonical_sport, "thesportsdb"),
                    away_team=resolve_team_name(db, str(away), canonical_sport, "thesportsdb"),
                    home_score=_to_int_or_none(item.get("intHomeScore")),
                    away_score=_to_int_or_none(item.get("intAwayScore")),
                    source="thesportsdb",
                    extra={"event_id": item.get("idEvent"), "provider_sport": provider_sport, "status": item.get("strStatus")},
                )
                upsert_fixture(db, fx)
                count += 1
            db.commit()
    return count


def load_football_csv(db: Session, path: str, league: str = "Unknown", season: str = "Unknown") -> int:
    header = read_csv_flexible(path, nrows=0).columns
    df = read_csv_flexible(path).rename(columns={k: v for k, v in FOOTBALL_DATA_MAP.items() if k in header})
    count = 0
    for _, r in df.iterrows():
        if not {"match_date", "home_team", "away_team"}.issubset(df.columns):
            continue
        parsed_date = pd.to_datetime(r["match_date"], dayfirst=True, errors="coerce")
        if pd.isna(parsed_date):
            continue
        fx = Fixture(
            sport="soccer",
            league=league,
            season=season,
            match_date=parsed_date.date(),
            home_team=resolve_team_name(db, str(r["home_team"]), "soccer", Path(path).name),
            away_team=resolve_team_name(db, str(r["away_team"]), "soccer", Path(path).name),
            home_score=None if pd.isna(r.get("home_score")) else int(r.get("home_score")),
            away_score=None if pd.isna(r.get("away_score")) else int(r.get("away_score")),
            home_odds=None if pd.isna(r.get("home_odds")) else float(r.get("home_odds")),
            draw_odds=None if pd.isna(r.get("draw_odds")) else float(r.get("draw_odds")),
            away_odds=None if pd.isna(r.get("away_odds")) else float(r.get("away_odds")),
            source=Path(path).name,
        )
        upsert_fixture(db, fx)
        count += 1
    db.commit()
    return count


def load_basketball_csv(db: Session, path: str, league: str = "NBA", season: str = "Unknown") -> int:
    """Load basketball historical CSVs from Kaggle/GitHub-style datasets.

    Expected fields can be any of the common names in BASKETBALL_DATA_MAP.
    Rows are normalized into the same fixtures table with sport='basketball'.
    """

    header = read_csv_flexible(path, nrows=0).columns
    df = read_csv_flexible(path).rename(columns={k: v for k, v in BASKETBALL_DATA_MAP.items() if k in header})

    # Kaggle/GitHub NBA files often store one row per team, not one row per game.
    # If a file has matchup strings like "LAL vs. BOS" / "BOS @ LAL", normalize
    # those rows into home/away fixtures before using the generic loader path.
    matchup_col = next((c for c in ["MATCHUP", "matchup"] if c in df.columns), None)
    points_col = next((c for c in ["PTS", "points", "TEAM_PTS"] if c in df.columns), None)
    team_col = next((c for c in ["TEAM_NAME", "team_name", "TEAM_ABBREVIATION"] if c in df.columns), None)
    game_col = next((c for c in ["GAME_ID", "game_id"] if c in df.columns), None)
    if matchup_col and points_col and team_col and game_col and "home_team" not in df.columns:
        rows = []
        for _, g in df.groupby(game_col):
            if len(g) < 2:
                continue
            home = away = None
            for _, r in g.iterrows():
                matchup = str(r.get(matchup_col, ""))
                if " vs. " in matchup or " vs " in matchup:
                    home = r
                elif " @ " in matchup:
                    away = r
            if home is None or away is None:
                continue
            rows.append({
                "match_date": home.get("match_date"),
                "home_team": home.get(team_col),
                "away_team": away.get(team_col),
                "home_score": home.get(points_col),
                "away_score": away.get(points_col),
            })
        df = pd.DataFrame(rows)
    count = 0
    for _, r in df.iterrows():
        if not {"match_date", "home_team", "away_team"}.issubset(df.columns):
            continue
        parsed_date = pd.to_datetime(r["match_date"], errors="coerce")
        if pd.isna(parsed_date):
            continue
        fx = Fixture(
            sport="basketball",
            league=league,
            season=season,
            match_date=parsed_date.date(),
            home_team=resolve_team_name(db, str(r["home_team"]), "basketball", Path(path).name),
            away_team=resolve_team_name(db, str(r["away_team"]), "basketball", Path(path).name),
            home_score=None if pd.isna(r.get("home_score")) else int(r.get("home_score")),
            away_score=None if pd.isna(r.get("away_score")) else int(r.get("away_score")),
            source=Path(path).name,
        )
        upsert_fixture(db, fx)
        count += 1
    db.commit()
    return count
