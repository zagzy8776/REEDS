"""Generate the published AI read for one exact fixture."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import Fixture, Prediction
from app.ml.generic import GenericSportEngine
from app.ml.ensemble import LoyalEdgeEngine
from app.services.model_registry import active_model_path
from app.services.predictions import (
    _backfill_fixture_odds,
    _capture_odds_snapshot,
    _next_prediction_version,
    explain_prediction_item,
    select_public_picks,
    dataframe_from_db,
)
from app.services.prediction_signature import (
    prediction_signature as _prediction_signature,
    existing_prediction_changed as _existing_prediction_changed,
)
from app.services.prediction_quality import annotate_quality

log = logging.getLogger(__name__)
