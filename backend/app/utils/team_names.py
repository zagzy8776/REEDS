import re


FOOTBALL_ALIASES = {
    "man united": "Manchester United",
    "man utd": "Manchester United",
    "manchester utd": "Manchester United",
    "manchester united": "Manchester United",
    "manchester united fc": "Manchester United",
    "man city": "Manchester City",
    "manchester city": "Manchester City",
    "manchester city fc": "Manchester City",
    "spurs": "Tottenham Hotspur",
    "tottenham": "Tottenham Hotspur",
    "tottenham hotspur": "Tottenham Hotspur",
    "tottenham hotspur fc": "Tottenham Hotspur",
    "wolves": "Wolverhampton Wanderers",
    "wolverhampton": "Wolverhampton Wanderers",
    "wolverhampton wanderers": "Wolverhampton Wanderers",
    "newcastle": "Newcastle United",
    "newcastle united": "Newcastle United",
    "newcastle united fc": "Newcastle United",
    "west ham": "West Ham United",
    "west ham united": "West Ham United",
    "west ham united fc": "West Ham United",
    "brighton": "Brighton & Hove Albion",
    "brighton hove albion": "Brighton & Hove Albion",
    "brighton and hove albion": "Brighton & Hove Albion",
    "inter milan": "Inter",
    "internazionale": "Inter",
    "inter": "Inter",
    "ac milan": "Milan",
    "milan": "Milan",
    "real madrid": "Real Madrid",
    "real madrid cf": "Real Madrid",
    "barca": "Barcelona",
    "fc barcelona": "Barcelona",
    "barcelona": "Barcelona",
    "ath madrid": "Atletico Madrid",
    "atletico madrid": "Atletico Madrid",
    "atletico de madrid": "Atletico Madrid",
    "wrexham": "Wrexham",
    "wrexham afc": "Wrexham",
    "wrexham fc": "Wrexham",
    "nottingham forest": "Nottingham Forest",
    "nottm forest": "Nottingham Forest",
    "leeds": "Leeds United",
    "leeds united": "Leeds United",
    "leeds united fc": "Leeds United",
    "leicester": "Leicester City",
    "leicester city": "Leicester City",
    "leicester city fc": "Leicester City",
    "southampton": "Southampton",
    "southampton fc": "Southampton",
    "ipswich": "Ipswich Town",
    "ipswich town": "Ipswich Town",
    "norwich": "Norwich City",
    "norwich city": "Norwich City",
    "sheffield united": "Sheffield United",
    "sheff utd": "Sheffield United",
    "sheffield wednesday": "Sheffield Wednesday",
    "sheff wed": "Sheffield Wednesday",
    "middlesbrough": "Middlesbrough",
    "hull": "Hull City",
    "hull city": "Hull City",
    "coventry": "Coventry City",
    "coventry city": "Coventry City",
    "bristol city": "Bristol City",
    "watford": "Watford",
    "watford fc": "Watford",
    "qpr": "Queens Park Rangers",
    "queens park rangers": "Queens Park Rangers",
    "millwall": "Millwall",
    "swansea": "Swansea City",
    "swansea city": "Swansea City",
    "cardiff": "Cardiff City",
    "cardiff city": "Cardiff City",
    "plymouth": "Plymouth Argyle",
    "plymouth argyle": "Plymouth Argyle",
    "oxford united": "Oxford United",
    "derby": "Derby County",
    "derby county": "Derby County",
    "stoke": "Stoke City",
    "stoke city": "Stoke City",
    "preston": "Preston North End",
    "preston north end": "Preston North End",
    "blackburn": "Blackburn Rovers",
    "blackburn rovers": "Blackburn Rovers",
    "burnley": "Burnley",
    "burnley fc": "Burnley",
    "luton": "Luton Town",
    "luton town": "Luton Town",
    "sunderland": "Sunderland",
    "sunderland afc": "Sunderland",
    "portsmouth": "Portsmouth",
    "portsmouth fc": "Portsmouth",
    "charlton": "Charlton Athletic",
    "charlton athletic": "Charlton Athletic",
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

_SUFFIX_RE = re.compile(
    r"\b(fc|afc|cf|sc|ac|fk|nk|sk|bk|if|ik|sv|as|ssc|calcio|united fc|city fc)\b$",
    re.IGNORECASE,
)


def _key(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", str(name).lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _strip_suffixes(key: str) -> str:
    """Peel trailing FC/AFC-style suffixes so aliases still match."""
    prev = None
    while prev != key:
        prev = key
        key = _SUFFIX_RE.sub("", key).strip()
        key = re.sub(r"\s+", " ", key).strip()
    return key


def normalize_team_name(name: str, sport: str = "soccer") -> str:
    """Normalize team names before storage/training.

    Prevents duplicate identities like "Man Utd", "Man United",
    "West Ham United FC", and "Wrexham AFC" from becoming separate teams.
    """
    aliases = BASKETBALL_ALIASES if sport == "basketball" else FOOTBALL_ALIASES
    key = _key(name)
    if key in aliases:
        return aliases[key]
    stripped = _strip_suffixes(key)
    if stripped in aliases:
        return aliases[stripped]
    if stripped != key and stripped:
        return stripped.title() if sport == "soccer" else str(name).strip()
    return str(name).strip()
