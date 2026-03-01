"""
Block 5 — Comparator + node tier endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Drug
from app.services.comparator_engine import get_adjacent_conditions, get_similar_drugs
from app.services.drug_summary_builder import build_drug_short
from app.services.mechanism_vocab import MECH_NODES_HASH, MECH_VOCAB_VERSION
from app.routes.vectorize import _get_or_compute_vector
from app.services.node_tiering import compute_node_tiers

router = APIRouter(prefix="/drug", tags=["comparator"])


@router.get("/{drug_id}/comparators")
def get_comparators(
    drug_id: str,
    top_k: int = 10,
    db: Session = Depends(get_db),
) -> dict:
    """
    Return mechanistically similar drugs and adjacent clinical conditions.
    """
    drug = db.get(Drug, drug_id)
    if drug is None:
        raise HTTPException(status_code=404, detail=f"Drug {drug_id} not found.")

    mv = _get_or_compute_vector("drug", drug_id, db)
    if mv is None:
        raise HTTPException(
            status_code=404,
            detail=f"Drug {drug_id} cannot be vectorized.",
        )

    similar_drugs = get_similar_drugs(drug_id, db, top_k=top_k)
    adjacent_conditions = get_adjacent_conditions(similar_drugs, db, top_k=15)

    return {
        "drug_id": drug_id,
        "canonical_name": drug.canonical_name,
        "similar_drugs": similar_drugs,
        "adjacent_conditions": adjacent_conditions,
    }


@router.get("/{drug_id}/node_tiers")
def get_node_tiers(
    drug_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """
    Return mechanism node evidence tiers for a drug.
    """
    drug = db.get(Drug, drug_id)
    if drug is None:
        raise HTTPException(status_code=404, detail=f"Drug {drug_id} not found.")

    mv = _get_or_compute_vector("drug", drug_id, db)
    if mv is None:
        raise HTTPException(
            status_code=404,
            detail=f"Drug {drug_id} cannot be vectorized.",
        )

    drug_short = build_drug_short(drug_id, db) or {}
    node_weights = mv.sparse or {}
    nodes = compute_node_tiers(drug_short, node_weights)

    return {
        "drug_id": drug_id,
        "canonical_name": drug.canonical_name,
        "nodes": nodes,
    }
