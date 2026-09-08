"""Public community API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.community import (
    community_leaderboard,
    community_overview,
    daily_challenge,
    experts_list,
    fixture_consensus,
    follow_user,
    prediction_social_context,
    user_profile,
    win_wall,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/community/leaderboard")
def get_community_leaderboard(limit: int = 50, db: Session = Depends(get_db)):
    return community_leaderboard(db, limit=limit)


@router.get("/community/overview")
def get_community_overview(db: Session = Depends(get_db)):
    return community_overview(db)


@router.get("/community/experts")
def get_community_experts(limit: int = 50, db: Session = Depends(get_db)):
    return experts_list(db, limit=limit)


@router.get("/community/win-wall")
def get_community_win_wall(limit: int = 30, db: Session = Depends(get_db)):
    return win_wall(db, limit=limit)


@router.get("/community/daily-challenge")
def get_community_daily_challenge(db: Session = Depends(get_db)):
    return daily_challenge(db)


@router.get("/community/profile/{username}")
def get_community_profile(username: str, db: Session = Depends(get_db)):
    profile = user_profile(db, username)
    if profile is None:
        raise HTTPException(status_code=404, detail="User not found")
    return profile


@router.post("/community/follow")
def post_community_follow(payload: dict, db: Session = Depends(get_db)):
    follower = str(payload.get("follower", "")).strip()
    following = str(payload.get("following", "")).strip()
    if not follower or not following:
        raise HTTPException(status_code=400, detail="follower and following are required")
    return follow_user(db, follower, following)
