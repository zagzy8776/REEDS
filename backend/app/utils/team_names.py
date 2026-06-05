import re


FOOTBALL_ALIASES = {
    "man united": "Manchester United",
    "man utd": "Manchester United",
    "manchester utd": "Manchester United",
    "manchester united": "Manchester United",
    "man city": "Manchester City",
    "manchester city": "Manchester City",
    "spurs": "Tottenham Hotspur",
    "tottenham": "Tottenham Hotspur",
    "tottenham hotspur": "Tottenham Hotspur",
    "wolves": "Wolverhampton Wanderers",
    "wolverhampton": "Wolverhampton Wanderers",
    "wolverhampton wanderers": "Wolverhampton Wanderers",
    "newcastle": "Newcastle United",
    "newcastle united": "Newcastle United",
    "west ham": "West Ham United",
    "west ham united": "West Ham United",
    "brighton": "Brighton & Hove Albion",
    "brighton hove albion": "Brighton & Hove Albion",
    "inter milan": "Inter",
    "internazionale": "Inter",
    "inter": "Inter",
    "ac milan": "Milan",
    "milan": "Milan",
    "real madrid": "Real Madrid",
    "barca": "Barcelona",
    "fc barcelona": "Barcelona",
    "barcelona": "Barcelona",
    "ath madrid": "Atletico Madrid",
    "atletico madrid": "Atletico Madrid",
}

BASKETBALL_ALIASES = {
    "la lakers": "Los Angeles Lakers",
    "los angeles lakers": "Los Angeles Lakers",
    "lakers": "Los Angeles Lakers",
    "la clippers": "Los Angeles Clippers",
    "los angeles clippers": "Los Angeles Clippers",
    "clippers": "Los Angeles Clippers",
    "gs warriors": "Golden State Warriors",
    "golden state warriors": "Golden State Warriors",
    "warriors": "Golden State Warriors",
    "ny knicks": "New York Knicks",
    "new york knicks": "New York Knicks",
    "knicks": "New York Knicks",
    "brooklyn nets": "Brooklyn Nets",
    "bkn nets": "Brooklyn Nets",
}


def _key(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", str(name).lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_team_name(name: str, sport: str = "soccer") -> str:
    """Normalize team names before storage/training.

    Prevents duplicate identities like "Man Utd", "Man United", and
    "Manchester United" from becoming separate teams in model memory.
    """
    aliases = BASKETBALL_ALIASES if sport == "basketball" else FOOTBALL_ALIASES
    key = _key(name)
    if key in aliases:
        return aliases[key]
    return str(name).strip()
