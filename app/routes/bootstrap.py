"""
Bootstrap routes: seed and run. Disabled unless ENABLE_BOOTSTRAP_ROUTES.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Disease, MechanismVector
from app.routes.vectorize import _get_or_compute_vector, _load_disease_short
from app.services import resolver
from app.services.bootstrap_seed import (
    build_why_summary,
    ensure_bootstrap_seed,
    load_bootstrap_config,
)
from app.services.drug_summary_builder import build_drug_short
from app.services.mechanism_vocab import MECH_NODES_HASH, MECH_VOCAB_VERSION
from app.services.scoring import score_pair

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])


class SeedRequest(BaseModel):
    force: bool = False
    ingest_mode: str = Field(default="sync", pattern="^(sync|async)$")
    poll_timeout_s: int = Field(default=180, ge=10, le=600)


class RunRequest(BaseModel):
    drug_name: str = Field(..., min_length=1, max_length=256)
    top_k: int = Field(default=10, ge=1, le=200)
    restrict_to_bootstrap_diseases: bool = True


@router.post("/seed")
def bootstrap_seed(
    body: SeedRequest | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Run bootstrap seed: ingest + vectorize drugs and diseases from config."""
    opts = body or SeedRequest()
    return ensure_bootstrap_seed(
        db,
        ingest_mode=opts.ingest_mode,
        poll_timeout_s=opts.poll_timeout_s,
        force=opts.force,
    )


@router.post("/run")
def bootstrap_run(
    body: RunRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    Ensure bootstrap seed, resolve drug, score against diseases, return UI-ready response.
    """
    cfg = load_bootstrap_config()
    ensure_bootstrap_seed(db, ingest_mode="sync", force=False)

    drug_name = body.drug_name.strip()
    ctx = resolver.resolve(db, drug_name)
    drug_id = ctx.drug_id

    if build_drug_short(drug_id, db) is None:
        from app.tasks.ingest import run_pipeline
        ctx_dict = {
            "drug_id": ctx.drug_id,
            "canonical_name": ctx.canonical_name,
            "input_name": ctx.input_name,
            "synonyms": list(ctx.synonyms),
            "identifiers": ctx.identifiers,
        }
        run_pipeline(ctx.drug_id, ctx_dict)
        db.commit()

    drug_mv = _get_or_compute_vector("drug", drug_id, db)
    if drug_mv is None:
        raise HTTPException(status_code=404, detail=f"Drug {drug_name} not found or cannot be vectorized.")

    drug_short = build_drug_short(drug_id, db) or {}
    drug_dense = drug_mv.dense_weights or []
    drug_sparse = drug_mv.sparse or {}

    if body.restrict_to_bootstrap_diseases:
        disease_queries = cfg["diseases"]
        disease_ids = []
        from app.services.disease_resolver import resolve_disease_query
        for q in disease_queries:
            resolved = resolve_disease_query(q)
            canonical = resolved.get("canonical_name") or q
            row = db.execute(select(Disease.id).where(Disease.canonical_name == canonical)).scalar_one_or_none()
            if row:
                disease_ids.append(row)
        if not disease_ids:
            disease_ids = [
                r.entity_id
                for r in db.execute(
                    select(MechanismVector).where(
                        MechanismVector.entity_type == "disease",
                        MechanismVector.vocab_version == MECH_VOCAB_VERSION,
                        MechanismVector.nodes_hash == MECH_NODES_HASH,
                    )
                ).scalars().all()
            ]
    else:
        rows = db.execute(
            select(MechanismVector.entity_id).where(
                MechanismVector.entity_type == "disease",
                MechanismVector.vocab_version == MECH_VOCAB_VERSION,
                MechanismVector.nodes_hash == MECH_NODES_HASH,
            )
        ).scalars().all()
        disease_ids = [r[0] for r in rows]

    scored: list[dict] = []
    for disease_id in disease_ids:
        disease_mv = _get_or_compute_vector("disease", disease_id, db)
        if disease_mv is None:
            continue
        disease_short = _load_disease_short(disease_id, db)
        if disease_short is None:
            continue
        disease_dense = disease_mv.dense_weights or []
        disease_sparse = disease_mv.sparse or {}

        out = score_pair(
            drug_short,
            disease_short,
            drug_dense,
            disease_dense,
            drug_sparse,
            disease_sparse,
        )
        mech_out = (out["breakdown"].get("mechanism") or {})
        top_nodes = mech_out.get("top_nodes") or []
        mech_score = float(mech_out.get("score") or 0)
        mechanism_nonzero = mech_score > 0 and len(top_nodes) >= 1
        why = build_why_summary(
            out["breakdown"],
            drug_short,
            disease_short,
            drug_sparse,
            top_nodes,
        )
        canonical_name = (disease_short.get("canonical_name") or "").strip() or disease_id
        comparators_top_k = cfg.get("comparators_top_k", 10)
        scored.append({
            "disease_id": disease_id,
            "canonical_name": canonical_name,
            "final_score": out["final_score"],
            "breakdown": out["breakdown"],
            "top_nodes": top_nodes,
            "mechanism_nonzero": mechanism_nonzero,
            "evidence_url": f"/pair/{drug_id}/{disease_id}/evidence",
            "comparators_url": f"/drug/{drug_id}/comparators?top_k={comparators_top_k}",
            "why_summary": why,
        })

    scored.sort(key=lambda x: -x["final_score"])
    scored = scored[: body.top_k]

    import os
    config_source = cfg.get("_config_source", "defaults")
    scoring_demo_mode = os.environ.get("SCORING_DEMO_MODE", "").lower() in {"1", "true", "yes", "on"}

    return {
        "drug": {"drug_id": drug_id, "canonical_name": ctx.canonical_name},
        "candidate_diseases_count": len(disease_ids),
        "scored_diseases": scored,
        "meta": {
            "bootstrap_used": True,
            "config_source": config_source,
            "scoring_demo_mode": scoring_demo_mode,
        },
    }
