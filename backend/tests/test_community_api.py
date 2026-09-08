"""Tests for public community API routes."""

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, CommunityComment, CommunityPlay, CommunityReaction, UserPrediction, WinSlip, Fixture
from app.services.community import (
    community_leaderboard,
    community_overview,
    daily_challenge,
    experts_list,
    fixture_consensus,
    follow_user,
    user_profile,
    win_wall,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_community_leaderboard_empty(db):
    assert community_leaderboard(db) == []


def test_community_overview_empty(db):
    data = community_overview(db)
    assert data["total_posts"] == 0
    assert data["pending"] == 0
    assert data["settled"] == 0


def test_community_experts_empty(db):
    assert experts_list(db) == []


def test_community_win_wall_empty(db):
    assert win_wall(db) == []


def test_community_daily_challenge_empty(db):
    data = daily_challenge(db)
    assert data["active"] is False


def test_community_profile_not_found(db):
    assert user_profile(db, "nonexistent_user") is None


def test_community_profile_with_picks(db):
    fx = Fixture(sport="soccer", league="Test", season="2025", match_date=date.today(), home_team="A", away_team="B", source="test")
    db.add(fx)
    db.commit()
    pred = UserPrediction(username="tester", fixture_id=fx.id, market="1X2", pick="home", analysis_text="test")
    db.add(pred)
    db.commit()
    profile = user_profile(db, "tester")
    assert profile is not None
    assert profile["username"] == "tester"
    assert profile["total_posts"] == 1


def test_follow_user_toggle(db):
    res = follow_user(db, "alice", "bob")
    assert res["status"] == "followed"
    res2 = follow_user(db, "alice", "bob")
    assert res2["status"] == "unfollowed"


def test_fixture_consensus_empty(db):
    assert fixture_consensus(db, 999) == {"total": 0, "consensus": [], "entries": []}
