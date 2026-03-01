"""
Block 2 — Mechanism-space vectorization & search.

Endpoints:
  POST /vectorize/disease/{disease_id}      → compute + store disease vector
  POST /vectorize/drug/{drug_id}            → compute + store drug vector
  POST /search/diseases_for_drug            → cosine similarity search
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Disease, DiseaseArtifact, Drug, MechanismVector
from app.services.disease_summary_compactor import compact_disease_summary
from app.services.drug_summary_builder import build_drug_short
from app.services.mechanism_mapper import (
    cosine_similarity,
    disease_to_mech_vector,
    drug_to_mech_vector,
    sparse_to_dense_direction,
    sparse_to_dense_weights,
)
from app.services.mechanism_store import upsert_mechanism_vector
from app.services.mechanism_vocab import MECH_NODES, MECH_NODES_HASH, MECH_VOCAB_VERSION

logger = logging.getLogger(__name__)

router = APIRouter(tags=["vectorize"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_disease_short(disease_id: str, db: Session) -> dict | None:
    """Return disease_short_v1 dict from DB, or None if not found."""
    disease = db.get(Disease, disease_id)
    if disease is None:
        return None
    art = db.execute(
        select(DiseaseArtifact).where(
            DiseaseArtifact.disease_id == disease_id,
            DiseaseArtifact.kind == "summary_raw",
        )
    ).scalars().first()
    if art is None:
        return None
    return compact_disease_summary(art.payload or {})


def _vectorize_disease(disease_id: str, db: Session) -> MechanismVector:
    """Compute + upsert disease vector. Raises HTTPException on failure."""
    short = _load_disease_short(disease_id, db)
    if short is None:
        raise HTTPException(
            status_code=404,
            detail=f"Disease {disease_id} not found or has no ingested summary.",
        )
    sparse = disease_to_mech_vector(short)
    payload = {
        "vocab_version": MECH_VOCAB_VERSION,
        "nodes_hash": MECH_NODES_HASH,
        "dense_weights": sparse_to_dense_weights(sparse),
        "dense_direction": sparse_to_dense_direction(sparse),
        "sparse": sparse,
    }
    mv = upsert_mechanism_vector("disease", disease_id, payload, db)
    db.commit()
    return mv


def _vectorize_drug(drug_id: str, db: Session) -> MechanismVector:
    """Compute + upsert drug vector. Raises HTTPException on failure."""
    drug = db.get(Drug, drug_id)
    if drug is None:
        raise HTTPException(status_code=404, detail=f"Drug {drug_id} not found.")
    short = build_drug_short(drug_id, db)
    if short is None:
        raise HTTPException(status_code=404, detail=f"Drug {drug_id} summary unavailable.")
    sparse = drug_to_mech_vector(short)
    payload = {
        "vocab_version": MECH_VOCAB_VERSION,
        "nodes_hash": MECH_NODES_HASH,
        "dense_weights": sparse_to_dense_weights(sparse),
        "dense_direction": sparse_to_dense_direction(sparse),
        "sparse": sparse,
    }
    mv = upsert_mechanism_vector("drug", drug_id, payload, db)
    db.commit()
    return mv


def _get_or_compute_vector(
    entity_type: str,
    entity_id: str,
    db: Session,
) -> MechanismVector | None:
    """
    Return existing MechanismVector for current vocab, or compute+store it.
    Returns None if entity can't be loaded (missing drug/disease).
    Swallows errors gracefully so bulk search can proceed.
    """
    existing = db.execute(
        select(MechanismVector).where(
            MechanismVector.entity_type == entity_type,
            MechanismVector.entity_id == entity_id,
            MechanismVector.vocab_version == MECH_VOCAB_VERSION,
            MechanismVector.nodes_hash == MECH_NODES_HASH,
        )
    ).scalars().first()

    if existing is not None:
        return existing

    try:
        if entity_type == "disease":
            return _vectorize_disease(entity_id, db)
        else:
            return _vectorize_drug(entity_id, db)
    except HTTPException:
        return None
    except Exception as exc:
        logger.warning("vectorize_error entity=%s/%s err=%s", entity_type, entity_id, exc)
        return None


def _vector_response(entity_type: str, entity_id: str, mv: MechanismVector) -> dict:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "vocab_version": mv.vocab_version,
        "nodes_hash": mv.nodes_hash,
        "sparse": mv.sparse or {},
        "dense_weights": mv.dense_weights or [],
        "dense_direction": mv.dense_direction or [],
    }


# ---------------------------------------------------------------------------
# GET /vector/{entity_type}/{entity_id} — return stored MechanismVector
# ---------------------------------------------------------------------------
@router.get("/vector/{entity_type}/{entity_id}", tags=["vectorize"])
def get_stored_vector(
    entity_type: str,
    entity_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """Return stored mechanism vector (sparse + dense + version/hash). 404 if missing."""
    if entity_type not in ("drug", "disease"):
        raise HTTPException(status_code=400, detail="entity_type must be 'drug' or 'disease'")
    mv = db.execute(
        select(MechanismVector).where(
            MechanismVector.entity_type == entity_type,
            MechanismVector.entity_id == entity_id,
            MechanismVector.vocab_version == MECH_VOCAB_VERSION,
            MechanismVector.nodes_hash == MECH_NODES_HASH,
        )
    ).scalars().first()
    if mv is None:
        raise HTTPException(
            status_code=404,
            detail=f"No stored vector for {entity_type}/{entity_id}. Run POST /vectorize/{entity_type}/{entity_id} first.",
        )
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "vocab_version": mv.vocab_version,
        "nodes_hash": mv.nodes_hash,
        "sparse": mv.sparse or {},
        "dense_weights": mv.dense_weights or [],
        "dense_direction": mv.dense_direction or [],
    }


# ---------------------------------------------------------------------------
# POST /vectorize/batch/diseases
# ---------------------------------------------------------------------------
class BatchDiseasesRequest(BaseModel):
    disease_ids: list[str] = Field(..., min_length=1, max_length=200)
    skip_existing: bool = True


@router.post("/vectorize/batch/diseases", tags=["vectorize"])
def batch_vectorize_diseases(
    body: BatchDiseasesRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Vectorize/upsert each disease. Returns ok count, skipped count, and errors."""
    ok = 0
    skipped = 0
    errors: list[dict] = []
    for disease_id in body.disease_ids:
        try:
            if body.skip_existing:
                existing = db.execute(
                    select(MechanismVector).where(
                        MechanismVector.entity_type == "disease",
                        MechanismVector.entity_id == disease_id,
                        MechanismVector.vocab_version == MECH_VOCAB_VERSION,
                        MechanismVector.nodes_hash == MECH_NODES_HASH,
                    )
                ).scalars().first()
                if existing is not None:
                    skipped += 1
                    continue
            mv = _vectorize_disease(disease_id, db)
            if mv is not None:
                ok += 1
        except HTTPException as e:
            errors.append({"disease_id": disease_id, "error": e.detail or str(e)})
        except Exception as e:
            errors.append({"disease_id": disease_id, "error": str(e)})
    return {"ok": ok, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# POST /vectorize/disease/{disease_id}
# ---------------------------------------------------------------------------
@router.post("/vectorize/disease/{disease_id}", tags=["vectorize"])
def vectorize_disease(disease_id: str, db: Session = Depends(get_db)) -> dict:
    """Compute mechanism vector for a disease and persist it."""
    mv = _vectorize_disease(disease_id, db)
    return _vector_response("disease", disease_id, mv)


# ---------------------------------------------------------------------------
# POST /vectorize/drug/{drug_id}
# ---------------------------------------------------------------------------
@router.post("/vectorize/drug/{drug_id}", tags=["vectorize"])
def vectorize_drug(drug_id: str, db: Session = Depends(get_db)) -> dict:
    """Compute mechanism vector for a drug and persist it."""
    mv = _vectorize_drug(drug_id, db)
    return _vector_response("drug", drug_id, mv)


# ---------------------------------------------------------------------------
# POST /search/diseases_for_drug
# ---------------------------------------------------------------------------
class DiseaseSearchRequest(BaseModel):
    drug_id: str
    disease_ids: list[str] | None = Field(
        default=None,
        description="Disease IDs to search. Omit to search all vectorized diseases.",
    )
    top_k: int = Field(default=20, ge=1, le=200)


@router.post("/search/diseases_for_drug", tags=["vectorize"])
def search_diseases_for_drug(
    body: DiseaseSearchRequest,
    db: Session = Depends(get_db),
) -> list[dict]:
    """
    Cosine similarity search: drug mechanism vector vs. disease vectors.

    - Ensures drug vector exists (computes if missing).
    - If disease_ids provided: ensures vectors exist for each (computes missing).
    - If disease_ids omitted: searches all already-vectorized diseases in DB.
    - Returns top_k matches sorted by cosine score desc.
    - top_nodes: top-5 overlapping nodes by min(drug_w, disease_w).
    """
    # 1. Ensure drug vector
    drug_mv = _get_or_compute_vector("drug", body.drug_id, db)
    if drug_mv is None:
        raise HTTPException(
            status_code=404,
            detail=f"Drug {body.drug_id} not found or cannot be vectorized.",
        )

    drug_weights: list[float] = drug_mv.dense_weights or []
    drug_sparse: dict = drug_mv.sparse or {}

    if not any(w > 0 for w in drug_weights):
        return []  # drug has zero vector — no signal to match

    # 2. Collect disease vectors
    if body.disease_ids is not None:
        # Explicit list: compute missing vectors
        disease_mvs: list[MechanismVector] = []
        for did in body.disease_ids:
            mv = _get_or_compute_vector("disease", did, db)
            if mv is not None:
                disease_mvs.append(mv)
    else:
        # Use all already-vectorized diseases in DB for current vocab
        rows = db.execute(
            select(MechanismVector).where(
                MechanismVector.entity_type == "disease",
                MechanismVector.vocab_version == MECH_VOCAB_VERSION,
                MechanismVector.nodes_hash == MECH_NODES_HASH,
            )
        ).scalars().all()
        disease_mvs = list(rows)

    if not disease_mvs:
        return []

    # 3. Score + rank
    scored: list[dict] = []
    for dmv in disease_mvs:
        disease_weights: list[float] = dmv.dense_weights or []
        score = cosine_similarity(drug_weights, disease_weights)
        if score <= 0.0:
            continue
        disease_sparse: dict = dmv.sparse or {}
        top_nodes = _top_overlapping_nodes(drug_sparse, disease_sparse, n=5)
        scored.append({
            "disease_id": dmv.entity_id,
            "score": score,
            "top_nodes": top_nodes,
        })

    scored.sort(key=lambda x: -x["score"])
    return scored[: body.top_k]


def _top_overlapping_nodes(
    drug_sparse: dict,
    disease_sparse: dict,
    n: int = 5,
) -> list[dict]:
    """Return top-n shared nodes ranked by min(drug_weight, disease_weight)."""
    overlaps: list[dict] = []
    for node in drug_sparse:
        if node not in disease_sparse:
            continue
        dw = float(drug_sparse[node].get("weight", 0.0))
        diw = float(disease_sparse[node].get("weight", 0.0))
        if dw > 0.0 and diw > 0.0:
            overlaps.append({"node": node, "drug_w": dw, "disease_w": diw, "_min": min(dw, diw)})
    overlaps.sort(key=lambda x: (-x["_min"], x["node"]))
    return [{"node": o["node"], "drug_w": o["drug_w"], "disease_w": o["disease_w"]} for o in overlaps[:n]]
