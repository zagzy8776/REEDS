import argparse
import csv
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.append(str(Path(__file__).resolve().parents[1]))


FOOTBALL_DATA_LEAGUES = {
    "EPL": "E0",
    "CHAMPIONSHIP": "E1",
    "LEAGUE_ONE": "E2",
    "LEAGUE_TWO": "E3",
    "LA_LIGA": "SP1",
    "SERIE_A": "I1",
    "SERIE_B": "I2",
    "BUNDESLIGA": "D1",
    "BUNDESLIGA_2": "D2",
    "LIGUE_1": "F1",
    "LIGUE_2": "F2",
    "EREDIVISIE": "N1",
    "PORTUGAL": "P1",
    "TURKEY": "T1",
    "BELGIUM": "B1",
    "SCOTLAND": "SC0",
    "GREECE": "G1",
}


def season_code(start_year: int) -> str:
    """football-data.co.uk season folder format: 2000 -> 0001, 2024 -> 2425."""
    return f"{str(start_year)[-2:]}{str(start_year + 1)[-2:]}"


def download_file(url: str, output_path: Path, timeout: int = 20, retries: int = 3) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, timeout=timeout, headers={"User-Agent": "LOYAL-EDGE-DataFeeder/1.0"})
            if response.status_code == 200 and response.text.strip():
                output_path.write_bytes(response.content)
                return True
            if response.status_code in {403, 404}:
                return False
        except requests.RequestException:
            pass
        if attempt < retries:
            time.sleep(min(2 ** attempt, 8))
    return False


def download_football_data(start_year: int, end_year: int, output_dir: Path, leagues: list[str]) -> list[Path]:
    downloaded: list[Path] = []
    for year in range(start_year, end_year):
        code = season_code(year)
        for league_name in leagues:
            league_code = FOOTBALL_DATA_LEAGUES[league_name]
            url = f"https://www.football-data.co.uk/mmz4281/{code}/{league_code}.csv"
            path = output_dir / "football" / league_name.lower() / f"{league_name}_{code}.csv"
            if download_file(url, path):
                downloaded.append(path)
                print({"downloaded": str(path), "url": url}, flush=True)
            else:
                print({"skipped": url}, flush=True)
    return downloaded


def download_url_manifest(manifest_path: Path, output_dir: Path) -> list[Path]:
    """Download custom CSV URLs listed in a manifest.

    Manifest columns: sport,league,season,url,filename(optional)
    Use this for GitHub raw CSV links and exported Kaggle datasets.
    """
    downloaded: list[Path] = []
    with manifest_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sport = row.get("sport", "misc").strip().lower()
            league = row.get("league", "unknown").strip().lower().replace(" ", "_")
            season = row.get("season", "unknown").strip().replace("/", "-")
            url = row["url"].strip()
            filename = row.get("filename") or Path(urlparse(url).path).name or f"{league}_{season}.csv"
            path = output_dir / sport / league / season / filename
            if download_file(url, path):
                downloaded.append(path)
                print({"downloaded": str(path), "url": url}, flush=True)
            else:
                print({"skipped": url}, flush=True)
    return downloaded


def main() -> None:
    parser = argparse.ArgumentParser(description="Download historical CSV data into data/raw for LOYAL EDGE.")
    parser.add_argument("--start-year", type=int, default=2000)
    parser.add_argument("--end-year", type=int, default=2025, help="Exclusive end year. 2025 downloads through 2024/25.")
    parser.add_argument("--output-dir", default="data/raw")
    parser.add_argument("--leagues", default="EPL,LA_LIGA,SERIE_A,BUNDESLIGA,LIGUE_1")
    parser.add_argument("--manifest", help="Optional CSV manifest of custom football/basketball raw CSV URLs.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    leagues = [x.strip().upper() for x in args.leagues.split(",") if x.strip()]
    invalid = [x for x in leagues if x not in FOOTBALL_DATA_LEAGUES]
    if invalid:
        raise SystemExit(f"Unknown league(s): {invalid}. Valid: {sorted(FOOTBALL_DATA_LEAGUES)}")

    downloaded = download_football_data(args.start_year, args.end_year, output_dir, leagues)
    if args.manifest:
        downloaded.extend(download_url_manifest(Path(args.manifest), output_dir))
    print({"total_downloaded": len(downloaded)})


if __name__ == "__main__":
    main()
