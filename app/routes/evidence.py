"""
Block 4 — Evidence ledger router.

GET /pair/{drug_id}/{disease_id}/evidence: return structured evidence for a drug-disease pair.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.routes.vectorize import _get_or_compute_vector, _load_disease_short
from app.services.drug_summary_builder import build_drug_short
from app.services.evidence_ledger import (
    build_pair_evidence,
    get_pair_evidence,
    store_pair_evidence,
)
from app.services.scoring import score_pair

router = APIRouter(tags=["evidence"])


@router.get("/pair/{drug_id}/{disease_id}/evidence")
def get_pair_evidence_endpoint(
    drug_id: str,
    disease_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """
    Return structured evidence for a drug-disease pair.
    If not stored, recomputes from vectors + score_pair, stores, and returns.
    """
    payload = get_pair_evidence(db, drug_id, disease_id)
    if payload is not None:
        return payload

    drug_mv = _get_or_compute_vector("drug", drug_id, db)
    if drug_mv is None:
        raise HTTPException(
            status_code=404,
            detail=f"Drug {drug_id} not found or cannot be vectorized.",
        )

    disease_mv = _get_or_compute_vector("disease", disease_id, db)
    if disease_mv is None:
        raise HTTPException(
            status_code=404,
            detail=f"Disease {disease_id} not found or cannot be vectorized.",
        )

    drug_short = build_drug_short(drug_id, db) or {}
    disease_short = _load_disease_short(disease_id, db)
    if disease_short is None:
        raise HTTPException(
            status_code=404,
            detail=f"Disease {disease_id} has no summary.",
        )

    out = score_pair(
        drug_short,
        disease_short,
        drug_mv.dense_weights or [],
        disease_mv.dense_weights or [],
        drug_mv.sparse or {},
        disease_mv.sparse or {},
    )

    payload = build_pair_evidence(
        drug_short,
        disease_short,
        drug_mv.sparse or {},
        disease_mv.sparse or {},
        out["breakdown"],
        drug_id=drug_id,
        disease_id=disease_id,
    )
    store_pair_evidence(db, drug_id, disease_id, payload)
    db.commit()
    return payload
