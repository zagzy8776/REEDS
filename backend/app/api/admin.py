from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.ml.train import train_basketball_model, train_soccer_model
from app.services.model_registry import register_model
from app.services.predictions import dataframe_from_db, generate_today_predictions


router = APIRouter()


def require_admin(x_admin_key: str = Header(default="")):
    if x_admin_key != get_settings().admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")


@router.post("/train", dependencies=[Depends(require_admin)])
def train(db: Session = Depends(get_db)):
    data = dataframe_from_db(db)
    trained, skipped = [], []
    for sport, trainer in (("soccer", train_soccer_model), ("basketball", train_basketball_model)):
        try:
            result = trainer(data)
            mv = register_model(db, sport, result["model_type"], result["path"], result["accuracy"], result["sample_size"])
            trained.append({"sport": sport, **result, "active": mv.is_active})
        except ValueError as exc:
            skipped.append({"sport": sport, "reason": str(exc)})
    return {"status": "trained", "trained": trained, "skipped": skipped}


@router.post("/predict", dependencies=[Depends(require_admin)])
def predict(db: Session = Depends(get_db)):
    return {"generated": generate_today_predictions(db)}
