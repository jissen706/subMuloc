"""
Block 7 — Validation endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.validation_engine import validate_scoring_system

router = APIRouter(tags=["validation"])


@router.get("/validation/score_health")
def score_health(db: Session = Depends(get_db)) -> dict:
    """
    Run scoring validation harness and return health metrics + recommendations.
    """
    return validate_scoring_system(db)

