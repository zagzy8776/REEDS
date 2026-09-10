"""League string normalization for soccer difficulty mapping."""
from __future__ import annotations

import re

SOCCER_LEAGUE_DIFFICULTY = {
    "EPL": 1.00,
    "LA_LIGA": 0.98,
    "La Liga": 0.98,
    "SERIE_A": 0.96,
    "Serie A": 0.96,
    "BUNDESLIGA": 0.96,
    "LIGUE_1": 0.92,
    "CHAMPIONSHIP": 0.85,
    "EREDIVISIE": 0.84,
    "PORTUGAL": 0.82,
    "BELGIUM": 0.78,
    "SCOTLAND": 0.76,
    "TURKEY": 0.80,
    "SERIE B": 0.78,
    "LIGA PORTUGAL": 0.82,
    "PRIMEIRA LIGA": 0.82,
    "LA LIGA 2": 0.74,
    "BUNDESLIGA 2": 0.76,
    "LIGUE 2": 0.72,
    "MLS": 0.74,
    "J LEAGUE": 0.70,
    "A LEAGUE": 0.68,
    "SAUDI LEAGUE": 0.72,
    "CHINA SUPER LEAGUE": 0.66,
}


def normalize_league_key(league) -> str:
    if league is None:
        return ""
    raw = str(league).strip()
    if not raw:
        return ""
    key = raw.replace("_", " ").replace("-", " ")
    key = re.sub(r"(?i)^soccer\s*", "", key)
    key = re.sub(r"(?i)^football\s*", "", key)
    key = re.sub(r"\s+", " ", key).strip()
    compact = key.upper().replace(" ", "")
    aliases = {
        "CHAMPIONSHIP": "CHAMPIONSHIP",
        "EFLCHAMPIONSHIP": "CHAMPIONSHIP",
        "ENGLISHCHAMPIONSHIP": "CHAMPIONSHIP",
        "SKYBETCHAMPIONSHIP": "CHAMPIONSHIP",
        "EPL": "EPL",
        "PREMIERLEAGUE": "EPL",
        "ENGLISHPREMIERLEAGUE": "EPL",
        "LALIGA": "LA_LIGA",
        "SERIEA": "SERIE_A",
        "BUNDESLIGA": "BUNDESLIGA",
        "LIGUE1": "LIGUE_1",
        "EREDIVISIE": "EREDIVISIE",
        "MLS": "MLS",
        "SCOTTISHPREMIERSHIP": "SCOTLAND",
        "PREMIERSHIP": "SCOTLAND",
    }
    if compact in aliases:
        return aliases[compact]
    upper_map = {k.upper().replace(" ", ""): k for k in SOCCER_LEAGUE_DIFFICULTY}
    if compact in upper_map:
        return upper_map[compact]
    return raw


def soccer_league_difficulty(league) -> float:
    key = normalize_league_key(league)
    if key in SOCCER_LEAGUE_DIFFICULTY:
        return SOCCER_LEAGUE_DIFFICULTY[key]
    compact = key.upper().replace(" ", "").replace("_", "")
    for map_key, value in SOCCER_LEAGUE_DIFFICULTY.items():
        if map_key.upper().replace(" ", "").replace("_", "") == compact:
            return value
    return 0.75
