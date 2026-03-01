"""
Disease endpoints (Block 1): resolve, ingest, summary, summary_short.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Disease, DiseaseArtifact
from app.services.disease_summary_compactor import compact_disease_summary
from app.services.disease_ingest import ingest_disease
from app.services.disease_resolver import resolve_disease_query

router = APIRouter(prefix="/disease", tags=["disease"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ResolveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=512)


class ResolveResponse(BaseModel):
    canonical_name: str
    ids: dict[str, str | None]
    synonyms: list[str]
    resolver_notes: list[str]


class IngestRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=512)


class IngestResponse(BaseModel):
    disease_id: str
    canonical_name: str
    ids: dict[str, str | None]


# ---------------------------------------------------------------------------
# POST /disease/resolve
# ---------------------------------------------------------------------------
@router.post("/resolve", response_model=ResolveResponse)
def disease_resolve(body: ResolveRequest) -> ResolveResponse:
    out = resolve_disease_query(body.query)
    return ResolveResponse(
        canonical_name=out["canonical_name"],
        ids=out["ids"],
        synonyms=out["synonyms"],
        resolver_notes=out["resolver_notes"],
    )


# ---------------------------------------------------------------------------
# POST /disease/ingest
# ---------------------------------------------------------------------------
@router.post("/ingest", response_model=IngestResponse)
def disease_ingest_endpoint(body: IngestRequest, db: Session = Depends(get_db)) -> IngestResponse:
    resolved = resolve_disease_query(body.query)
    canonical_name = resolved["canonical_name"]
    ids = resolved["ids"]
    ids_json = {"orpha": ids.get("orpha"), "omim": ids.get("omim")}

    # Upsert disease (match on canonical_name; simple for hackathon)
    existing = db.execute(
        select(Disease).where(Disease.canonical_name == canonical_name)
    ).scalar_one_or_none()
    if existing:
        disease = existing
        disease.ids_json = ids_json
    else:
        disease = Disease(canonical_name=canonical_name, ids_json=ids_json)
        db.add(disease)
        db.flush()

    db.flush()
    db.refresh(disease)

    # Run ingestion synchronously (no DB; defensive)
    raw_summary = ingest_disease(disease, query_hint=body.query)
    raw_summary["disease_id"] = disease.id
    raw_summary["canonical_name"] = disease.canonical_name

    # Store artifact
    artifact = DiseaseArtifact(
        disease_id=disease.id,
        kind="summary_raw",
        payload=raw_summary,
    )
    db.add(artifact)
    db.commit()

    return IngestResponse(
        disease_id=disease.id,
        canonical_name=disease.canonical_name,
        ids=ids,
    )


# ---------------------------------------------------------------------------
# GET /disease/{disease_id}/summary
# ---------------------------------------------------------------------------
@router.get("/{disease_id}/summary")
def disease_summary(disease_id: str, db: Session = Depends(get_db)) -> dict:
    disease = db.get(Disease, disease_id)
    if not disease:
        raise HTTPException(status_code=404, detail="Disease not found")
    art = db.execute(
        select(DiseaseArtifact).where(
            DiseaseArtifact.disease_id == disease_id,
            DiseaseArtifact.kind == "summary_raw",
        )
    ).scalars().first()
    if not art:
        raise HTTPException(status_code=404, detail="No summary artifact found")
    return art.payload or {}


# ---------------------------------------------------------------------------
# GET /disease/{disease_id}/summary_short
# ---------------------------------------------------------------------------
@router.get("/{disease_id}/summary_short")
def disease_summary_short(disease_id: str, db: Session = Depends(get_db)) -> dict:
    disease = db.get(Disease, disease_id)
    if not disease:
        raise HTTPException(status_code=404, detail="Disease not found")
    art = db.execute(
        select(DiseaseArtifact).where(
            DiseaseArtifact.disease_id == disease_id,
            DiseaseArtifact.kind == "summary_raw",
        )
    ).scalars().first()
    if not art:
        raise HTTPException(status_code=404, detail="No summary artifact found")
    raw = art.payload or {}
    return compact_disease_summary(raw)
