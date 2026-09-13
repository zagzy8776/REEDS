"""Public community API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import CommunityComment, CommunityPlay, CommunityReaction, UserPrediction, WinSlip
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


@router.post("/community/predictions")
def post_community_prediction(payload: dict, db: Session = Depends(get_db)):
    username = str(payload.get("username", "")).strip()
    fixture_id = payload.get("fixture_id")
    market = str(payload.get("market", "")).strip()
    pick = str(payload.get("pick", "")).strip()
    analysis_text = payload.get("analysis_text")
    if not username or not fixture_id or not market or not pick:
        raise HTTPException(status_code=400, detail="username, fixture_id, market, and pick are required")
    row = UserPrediction(username=username, fixture_id=int(fixture_id), market=market, pick=pick, analysis_text=str(analysis_text) if analysis_text is not None else None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "fixture_id": row.fixture_id,
        "username": row.username,
        "market": row.market,
        "pick": row.pick,
        "analysis_text": row.analysis_text,
        "is_settled": row.is_settled,
        "was_correct": row.was_correct,
        "profit_units": row.profit_units,
        "created_at": row.created_at,
    }


@router.post("/community/win-slips")
def post_community_win_slip(payload: dict, db: Session = Depends(get_db)):
    username = str(payload.get("username", "")).strip()
    title = str(payload.get("title", "")).strip()
    proof_text = payload.get("proof_text")
    profit_units = payload.get("profit_units")
    prediction_id = payload.get("prediction_id")
    if not username or not title:
        raise HTTPException(status_code=400, detail="username and title are required")
    row = WinSlip(
        username=username,
        title=title,
        proof_text=str(proof_text) if proof_text is not None else None,
        profit_units=float(profit_units) if profit_units is not None else None,
        prediction_id=int(prediction_id) if prediction_id is not None else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "prediction_id": row.prediction_id,
        "username": row.username,
        "title": row.title,
        "proof_text": row.proof_text,
        "profit_units": row.profit_units,
        "created_at": row.created_at,
    }


@router.post("/community/predictions/{prediction_id}/plays")
def post_community_play(prediction_id: int, payload: dict, db: Session = Depends(get_db)):
    username = str(payload.get("username", "")).strip()
    stake_units = payload.get("stake_units")
    if not username:
        raise HTTPException(status_code=400, detail="username is required")
    row = CommunityPlay(
        prediction_id=prediction_id,
        username=username,
        stake_units=float(stake_units) if stake_units is not None else 1.0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "prediction_id": row.prediction_id,
        "username": row.username,
        "stake_units": row.stake_units,
        "status": row.status,
        "created_at": row.created_at,
    }


@router.post("/community/predictions/{prediction_id}/comments")
def post_community_comment(prediction_id: int, payload: dict, db: Session = Depends(get_db)):
    username = str(payload.get("username", "")).strip()
    comment_text = str(payload.get("comment_text", "")).strip()
    if not username or not comment_text:
        raise HTTPException(status_code=400, detail="username and comment_text are required")
    row = CommunityComment(prediction_id=prediction_id, username=username, comment_text=comment_text)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "prediction_id": row.prediction_id,
        "username": row.username,
        "comment_text": row.comment_text,
        "created_at": row.created_at,
    }


@router.post("/community/predictions/{prediction_id}/reactions")
def post_community_reaction(prediction_id: int, payload: dict, db: Session = Depends(get_db)):
    username = str(payload.get("username", "")).strip()
    reaction = str(payload.get("reaction", "like")).strip()
    rating = payload.get("rating")
    if not username:
        raise HTTPException(status_code=400, detail="username is required")
    row = CommunityReaction(
        prediction_id=prediction_id,
        username=username,
        reaction=reaction,
        rating=int(rating) if rating is not None else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "prediction_id": row.prediction_id,
        "username": row.username,
        "reaction": row.reaction,
        "rating": row.rating,
        "created_at": row.created_at,
    }
