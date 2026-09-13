import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.models import Fixture
from app.db.session import SessionLocal, init_db
from app.scraper.loaders import upsert_fixture
from app.services.data_quality import resolve_team_name


RAW_BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"


def fetch_json(url: str, timeout: int = 30, retries: int = 3):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, timeout=timeout, headers={"User-Agent": "LOYAL-EDGE-StatsBomb-Importer/1.0"})
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(min(2 * attempt, 6))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def load_cached_or_fetch(cache_path: Path, url: str, refresh: bool = False):
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    data = fetch_json(url)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def score_value(score: dict | None, side: str) -> int | None:
    if not isinstance(score, dict):
        return None
    value = score.get(side)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_match_date(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def import_statsbomb_matches(max_matches: int | None = None, refresh: bool = False) -> dict:
    """Import real StatsBomb open soccer match results into Fixture rows.

    StatsBomb open-data is historical soccer data. This importer uses match-level
    JSON only, which is enough to add real scored fixtures for model training
    without heavy event-file downloads.
    """

    cache_root = Path("data/raw/statsbomb_open_data")
    competitions = load_cached_or_fetch(cache_root / "competitions.json", f"{RAW_BASE}/competitions.json", refresh=refresh)

    init_db()
    db = SessionLocal()
    imported = 0
    competitions_seen = 0
    seasons_seen = 0
    try:
        for comp in competitions:
            competition_id = comp.get("competition_id")
            season_id = comp.get("season_id")
            if competition_id is None or season_id is None:
                continue
            competitions_seen += 1
            seasons_seen += 1
            league_name = comp.get("competition_name") or "StatsBomb Open Data"
            season_name = str(comp.get("season_name") or season_id)
            matches_url = f"{RAW_BASE}/matches/{competition_id}/{season_id}.json"
            matches_cache = cache_root / "matches" / str(competition_id) / f"{season_id}.json"
            try:
                matches = load_cached_or_fetch(matches_cache, matches_url, refresh=refresh)
            except Exception as exc:  # noqa: BLE001
                print({"skipped_competition": competition_id, "season": season_id, "reason": str(exc)}, flush=True)
                continue
            for match in matches:
                home = (match.get("home_team") or {}).get("home_team_name")
                away = (match.get("away_team") or {}).get("away_team_name")
                match_date = match.get("match_date")
                parsed_date = parse_match_date(match_date)
                if not home or not away or not parsed_date:
                    continue
                fx = Fixture(
                    sport="soccer",
                    league=str(league_name)[:80],
                    season=season_name[:20],
                    match_date=parsed_date,
                    home_team=resolve_team_name(db, str(home), "soccer", "statsbomb_open_data"),
                    away_team=resolve_team_name(db, str(away), "soccer", "statsbomb_open_data"),
                    home_score=score_value(match, "home_score"),
                    away_score=score_value(match, "away_score"),
                    source="statsbomb_open_data",
                    extra={
                        "match_id": match.get("match_id"),
                        "competition_id": competition_id,
                        "season_id": season_id,
                        "data_version": match.get("metadata", {}).get("data_version") if isinstance(match.get("metadata"), dict) else None,
                    },
                )
                upsert_fixture(db, fx)
                imported += 1
                if imported % 250 == 0:
                    db.commit()
                    print({"imported": imported}, flush=True)
                if max_matches and imported >= max_matches:
                    db.commit()
                    return {"imported": imported, "competitions_seen": competitions_seen, "seasons_seen": seasons_seen}
        db.commit()
        return {"imported": imported, "competitions_seen": competitions_seen, "seasons_seen": seasons_seen}
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import real StatsBomb open soccer match results into LOYAL EDGE.")
    parser.add_argument("--max-matches", type=int, default=None, help="Optional safety cap for first runs.")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached JSON from GitHub.")
    args = parser.parse_args()
    print(import_statsbomb_matches(max_matches=args.max_matches, refresh=args.refresh))


if __name__ == "__main__":
    main()