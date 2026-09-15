"""Tests for team name normalization and alias resolution."""
import pytest

from app.utils.team_names import normalize_team_name


class TestTeamNameNormalization:
    """Tests for normalize_team_name — prevents duplicate team identities."""

    @pytest.mark.parametrize("input_name,expected", [
        ("man utd", "Manchester United"),
        ("man united", "Manchester United"),
        ("manchester utd", "Manchester United"),
        ("manchester united fc", "Manchester United"),
        ("man city", "Manchester City"),
        ("spurs", "Tottenham Hotspur"),
        ("tottenham", "Tottenham Hotspur"),
        ("wolves", "Wolverhampton Wanderers"),
        ("west ham", "West Ham United"),
        ("brighton", "Brighton & Hove Albion"),
        ("brighton hove albion", "Brighton & Hove Albion"),
        ("brighton and hove albion", "Brighton & Hove Albion"),
        ("inter milan", "Inter"),
        ("inter", "Inter"),
        ("ac milan", "Milan"),
        ("milan", "Milan"),
        ("barca", "Barcelona"),
        ("fc barcelona", "Barcelona"),
        ("barcelona", "Barcelona"),
        ("ath madrid", "Atletico Madrid"),
        ("atletico madrid", "Atletico Madrid"),
        ("nottingham forest", "Nottingham Forest"),
        ("nottm forest", "Nottingham Forest"),
        ("leeds", "Leeds United"),
        ("leicester", "Leicester City"),
        ("ipswich", "Ipswich Town"),
        ("norwich", "Norwich City"),
        ("sheffield united", "Sheffield United"),
        ("sheff utd", "Sheffield United"),
        ("sheffield wednesday", "Sheffield Wednesday"),
        ("sheff wed", "Sheffield Wednesday"),
        ("wigan", "Wigan Athletic"),
        ("wigan athletic", "Wigan Athletic"),
        ("qpr", "Queens Park Rangers"),
        ("birmingham", "Birmingham City"),
        ("blackburn", "Blackburn Rovers"),
        ("preston", "Preston North End"),
        ("stoke city", "Stoke City"),
        ("brentford", "Brentford"),
        ("fulham", "Fulham"),
        ("bournemouth", "AFC Bournemouth"),
        ("arsenal fc", "Arsenal"),
        ("liverpool fc", "Liverpool"),
        ("chelsea fc", "Chelsea"),
        ("everton fc", "Everton"),
        ("wrexham afc", "Wrexham"),
        ("sunderland afc", "Sunderland"),
        ("burnley fc", "Burnley"),
        ("liverpool fc", "Liverpool"),
    ])
    def test_alias_normalization(self, input_name, expected):
        assert normalize_team_name(input_name, "soccer") == expected

    def test_no_alias_passes_through_with_suffix_strip(self):
        result = normalize_team_name("Chelsea FC", "soccer")
        assert result == "Chelsea"

    def test_basketball_alias(self):
        assert normalize_team_name("la lakers", "basketball") == "Los Angeles Lakers"
        assert normalize_team_name("gs warriors", "basketball") == "Golden State Warriors"

    def test_empty_string(self):
        assert normalize_team_name("", "soccer") == ""

    def test_case_insensitive(self):
        assert normalize_team_name("MAN UNITED", "soccer") == "Manchester United"
        assert normalize_team_name("ArSeNaL", "soccer") == "Arsenal"

    def test_unicode_handling(self):
        assert normalize_team_name("brøndby", "soccer") == "Brondby"

    def test_idempotent(self):
        name = normalize_team_name("man utd", "soccer")
        assert normalize_team_name(name, "soccer") == name
