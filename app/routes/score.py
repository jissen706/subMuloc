"""
Block 3 — Scoring engine router.

POST /score/drug_to_diseases: rank diseases for a drug with full breakdown.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MechanismVector
from app.routes.vectorize import _get_or_compute_vector, _load_disease_short
from app.services.drug_summary_builder import build_drug_short
from app.services.scoring import score_pair
from app.services.mechanism_vocab import MECH_NODES_HASH, MECH_VOCAB_VERSION

router = APIRouter(prefix="/score", tags=["score"])


class DrugToDiseasesRequest(BaseModel):
    drug_id: str
    disease_ids: list[str] | None = Field(
        default=None,
        description="Disease IDs to score. Omit to use all vectorized diseases.",
    )
    top_k: int = Field(default=20, ge=1, le=200)
    weights: dict[str, float] | None = Field(
        default=None,
        description="Optional weight overrides: mechanism, evidence, safety, uncertainty.",
    )
    include_evidence: bool = Field(
        default=False,
        description="If true, include first 3 mechanism_overlap items inline (do not store).",
    )


@router.post("/drug_to_diseases")
def drug_to_diseases(
    body: DrugToDiseasesRequest,
    db: Session = Depends(get_db),
) -> list[dict]:
    """
    Rank diseases for a given drug using mechanism similarity, evidence, safety penalty,
    and uncertainty penalty. Returns top_k with full breakdown. Skips diseases missing
    short summaries without crashing.
    """
    drug_mv = _get_or_compute_vector("drug", body.drug_id, db)
    if drug_mv is None:
        raise HTTPException(
            status_code=404,
            detail=f"Drug {body.drug_id} not found or cannot be vectorized.",
        )

    drug_dense: list[float] = drug_mv.dense_weights or []
    drug_sparse: dict = drug_mv.sparse or {}
    drug_short = build_drug_short(body.drug_id, db) or {}

    if body.disease_ids is not None:
        disease_mvs: list[MechanismVector] = []
        for did in body.disease_ids:
            mv = _get_or_compute_vector("disease", did, db)
            if mv is not None:
                disease_mvs.append(mv)
    else:
        rows = db.execute(
            select(MechanismVector).where(
                MechanismVector.entity_type == "disease",
                MechanismVector.vocab_version == MECH_VOCAB_VERSION,
                MechanismVector.nodes_hash == MECH_NODES_HASH,
            )
        ).scalars().all()
        disease_mvs = list(rows)

    results: list[dict] = []
    for dmv in disease_mvs:
        disease_id = dmv.entity_id
        disease_short = _load_disease_short(disease_id, db)
        if disease_short is None:
            continue
        disease_dense: list[float] = dmv.dense_weights or []
        disease_sparse: dict = dmv.sparse or {}

        out = score_pair(
            drug_short,
            disease_short,
            drug_dense,
            disease_dense,
            drug_sparse,
            disease_sparse,
            weights=body.weights,
        )
        canonical_name = (disease_short.get("canonical_name") or "").strip() or disease_id
        item: dict = {
            "disease_id": disease_id,
            "canonical_name": canonical_name,
            "final_score": out["final_score"],
            "breakdown": out["breakdown"],
        }
        if body.include_evidence:
            top_nodes = (out["breakdown"].get("mechanism") or {}).get("top_nodes") or []
            evidence_preview = [
                {
                    "node": n.get("node"),
                    "drug_weight": n.get("drug_w"),
                    "disease_weight": n.get("disease_w"),
                    "overlap_weight": min(float(n.get("drug_w") or 0), float(n.get("disease_w") or 0)),
                }
                for n in top_nodes[:3]
                if isinstance(n, dict)
            ]
            item["evidence"] = evidence_preview
        results.append(item)

    results.sort(key=lambda x: -x["final_score"])
    return results[: body.top_k]
