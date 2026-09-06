from sqlalchemy.orm import Session



def ensure_multisport_showcase(db: Session, min_upcoming_per_sport: int = 2) -> dict:
    """Compatibility no-op.

    REEDS production must never manufacture fixtures to make the board look full.
    Real fixtures come from the configured providers and coverage escalator.
    """
    return {}
