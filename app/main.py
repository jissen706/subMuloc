"""
Drug Intelligence Ingestion Platform — FastAPI application.

Endpoints:
  POST /drug/ingest        → resolve + enqueue/run ingestion pipeline
  GET  /drug/{id}/summary  → structured summary from DB
  GET  /health             → liveness probe
"""
from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import (
    ClinVarAssociation,
    DiseasePathwayMention,
    Drug,
    DrugIdentifier,
    DrugSynonym,
    Evidence,
    LabelWarning,
    MolecularStructure,
    Publication,
    Target,
    ToxicityMetric,
    Trial,
)
from app.schemas.drug import IngestRequest, IngestResponse
from app.schemas.summary import (
    ClinVarAssociationOut,
    DrugSummaryOut,
    IdentifierOut,
    LabelWarningOut,
    MolecularStructureOut,
    PathwayMentionOut,
    PublicationSummaryOut,
    TargetOut,
    ToxicityMetricOut,
    TrialOut,
    TrialSummaryOut,
)
from app.services import resolver
from app.services.summary_compactor import compact_drug_summary

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Drug Intelligence Ingestion Platform",
    version="1.0.0",
    description="Evidence ingestion + normalization for drug data from public sources.",
)

from app.routes.comparator import router as comparator_router
from app.routes.disease import router as disease_router
from app.routes.evidence import router as evidence_router
from app.routes.score import router as score_router
from app.routes.validation import router as validation_router
from app.routes.vectorize import router as vectorize_router

app.include_router(comparator_router)
app.include_router(disease_router)
app.include_router(evidence_router)
app.include_router(score_router)
app.include_router(validation_router)
app.include_router(vectorize_router)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /drug/ingest
# ---------------------------------------------------------------------------
@app.post("/drug/ingest", response_model=IngestResponse, tags=["ingestion"])
def ingest_drug(
    body: IngestRequest,
    db: Session = Depends(get_db),
) -> IngestResponse:
    """
    Resolve drug name, enqueue (or run sync) the ingestion pipeline,
    and return initial counts.
    """
    settings = get_settings()

    # 1. Resolve identifiers + synonyms
    try:
        ctx = resolver.resolve(db, body.name)
    except Exception as exc:
        logger.error("resolver_error name=%s err=%s", body.name, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Resolution failed: {exc}")

    ctx_dict = {
        "drug_id": ctx.drug_id,
        "canonical_name": ctx.canonical_name,
        "input_name": ctx.input_name,
        "synonyms": list(ctx.synonyms),
        "identifiers": ctx.identifiers,
    }

    # 2. Dispatch ingestion
    if settings.ingest_mode == "async":
        from app.tasks.ingest import ingest_drug as celery_task
        task = celery_task.delay(ctx.drug_id, ctx_dict)
        return IngestResponse(
            drug_id=ctx.drug_id,
            canonical_name=ctx.canonical_name,
            status="queued",
            task_id=task.id,
            message="Ingestion queued. Poll GET /drug/{id}/summary for results.",
        )
    else:
        # Synchronous mode (for local dev / testing)
        from app.tasks.ingest import run_pipeline
        try:
            counts = run_pipeline(ctx.drug_id, ctx_dict)
            return IngestResponse(
                drug_id=ctx.drug_id,
                canonical_name=ctx.canonical_name,
                status="completed",
                counts=counts,
            )
        except Exception as exc:
            logger.error("sync_pipeline_error drug=%s err=%s", ctx.canonical_name, exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")


# ---------------------------------------------------------------------------
# Summary builder (shared by /summary and /summary_short)
# ---------------------------------------------------------------------------
def _build_drug_summary(drug_id: str, db: Session) -> DrugSummaryOut:
    """Build full drug summary from DB. Raises HTTPException 404 if drug not found."""
    drug = db.get(Drug, drug_id)
    if drug is None:
        raise HTTPException(status_code=404, detail="Drug not found")

    # Identifiers
    identifiers = db.execute(
        select(DrugIdentifier).where(DrugIdentifier.drug_id == drug_id)
    ).scalars().all()

    # Synonyms
    synonyms_rows = db.execute(
        select(DrugSynonym).where(DrugSynonym.drug_id == drug_id)
    ).scalars().all()

    # Molecular structure
    structure = db.get(MolecularStructure, drug_id)

    # Targets
    targets = db.execute(
        select(Target).where(Target.drug_id == drug_id)
    ).scalars().all()

    # Trials
    trials = db.execute(
        select(Trial).where(Trial.drug_id == drug_id)
    ).scalars().all()

    # Publications
    pubs = db.execute(
        select(Publication).where(Publication.drug_id == drug_id)
    ).scalars().all()

    # Label warnings
    warnings = db.execute(
        select(LabelWarning).where(LabelWarning.drug_id == drug_id)
    ).scalars().all()

    # Toxicity metrics
    tox_metrics = db.execute(
        select(ToxicityMetric).where(ToxicityMetric.drug_id == drug_id)
    ).scalars().all()

    # Pathway mentions (aggregated by term)
    pathway_rows = db.execute(
        select(DiseasePathwayMention).where(DiseasePathwayMention.drug_id == drug_id)
    ).scalars().all()

    # ClinVar
    clinvar_rows = db.execute(
        select(ClinVarAssociation).where(ClinVarAssociation.drug_id == drug_id)
    ).scalars().all()

    # -----------------------------------------------------------------------
    # Build response
    # -----------------------------------------------------------------------

    # Trials summary
    by_phase: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    trial_outs: list[TrialOut] = []
    for t in trials:
        phase_key = t.phase or "Unknown"
        status_key = t.status or "Unknown"
        by_phase[phase_key] += 1
        by_status[status_key] += 1
        trial_outs.append(TrialOut(
            nct_id=t.nct_id,
            title=t.title,
            phase=t.phase,
            status=t.status,
            conditions=t.conditions_json if isinstance(t.conditions_json, list) else [],
            sponsor=t.sponsor,
            start_date=t.start_date,
            completion_date=t.completion_date,
            results_posted=t.results_posted,
            url=t.url,
        ))

    trial_summary = TrialSummaryOut(
        total=len(trials),
        by_phase=dict(by_phase),
        by_status=dict(by_status),
        trials=trial_outs,
    )

    # Publications summary
    by_year: dict[int, int] = defaultdict(int)
    for p in pubs:
        if p.year:
            by_year[p.year] += 1

    recent_pubs = sorted(
        [p for p in pubs if p.year],
        key=lambda p: p.year or 0,
        reverse=True,
    )[:10]

    pub_summary = PublicationSummaryOut(
        total=len(pubs),
        by_year=dict(by_year),
        recent=[
            {
                "pmid": p.pmid,
                "title": p.title,
                "year": p.year,
                "journal": p.journal,
                "url": p.url,
            }
            for p in recent_pubs
        ],
    )

    # Pathway mentions: aggregate by term
    pathway_agg: dict[str, dict] = {}
    for row in pathway_rows:
        term = row.pathway_term
        if term not in pathway_agg:
            pathway_agg[term] = {
                "count": 0,
                "max_confidence": 0.0,
                "sources": set(),
            }
        pathway_agg[term]["count"] += 1
        pathway_agg[term]["max_confidence"] = max(
            pathway_agg[term]["max_confidence"],
            row.confidence or 0.0,
        )
        pathway_agg[term]["sources"].add(row.evidence_source)

    pathway_out = sorted(
        [
            PathwayMentionOut(
                pathway_term=term,
                count=agg["count"],
                max_confidence=round(agg["max_confidence"], 2),
                evidence_sources=sorted(agg["sources"]),
            )
            for term, agg in pathway_agg.items()
        ],
        key=lambda x: (-x.count, -x.max_confidence),
    )

    return DrugSummaryOut(
        drug_id=drug_id,
        canonical_name=drug.canonical_name,
        identifiers=[IdentifierOut(id_type=i.id_type, value=i.value) for i in identifiers],
        synonyms=[s.synonym for s in synonyms_rows],
        molecular_structure=MolecularStructureOut(
            smiles=structure.smiles if structure else None,
            inchi=structure.inchi if structure else None,
            molecular_formula=structure.molecular_formula if structure else None,
            molecular_weight=structure.molecular_weight if structure else None,
        ) if structure else None,
        targets=[
            TargetOut(
                target_name=t.target_name,
                gene_symbol=t.gene_symbol,
                source=t.source,
                evidence=t.evidence,
            )
            for t in targets
        ],
        trials=trial_summary,
        publications=pub_summary,
        label_warnings=[
            LabelWarningOut(section=w.section, text=w.text, url=w.url)
            for w in warnings
        ],
        toxicity_metrics=[
            ToxicityMetricOut(
                metric_type=m.metric_type,
                value=m.value,
                units=m.units,
                interpreted_flag=m.interpreted_flag,
                evidence_source=m.evidence_source,
                evidence_ref=m.evidence_ref,
                notes=m.notes,
            )
            for m in tox_metrics
        ],
        pathway_mentions=pathway_out,
        clinvar_associations=[
            ClinVarAssociationOut(
                gene_symbol=c.gene_symbol,
                variant=c.variant,
                clinical_significance=c.clinical_significance,
                condition=c.condition,
                url=c.url,
            )
            for c in clinvar_rows
        ],
    )


# ---------------------------------------------------------------------------
# GET /drug/{id}/summary
# ---------------------------------------------------------------------------
@app.get("/drug/{drug_id}/summary", response_model=DrugSummaryOut, tags=["query"])
def get_drug_summary(drug_id: str, db: Session = Depends(get_db)) -> DrugSummaryOut:
    """Return structured JSON summary of everything ingested for a drug."""
    return _build_drug_summary(drug_id, db)


# ---------------------------------------------------------------------------
# GET /drug/{id}/summary_short
# ---------------------------------------------------------------------------
@app.get("/drug/{drug_id}/summary_short", tags=["query"])
def get_drug_summary_short(drug_id: str, db: Session = Depends(get_db)) -> dict:
    """Return compact JSON summary for UI + scoring (same data as /summary, compacted)."""
    summary_out = _build_drug_summary(drug_id, db)
    raw = summary_out.model_dump()
    return compact_drug_summary(raw)


# ---------------------------------------------------------------------------
# GET /drug/{id}/task-status  (Celery async status check)
# ---------------------------------------------------------------------------
@app.get("/drug/{drug_id}/task/{task_id}/status", tags=["ingestion"])
def get_task_status(drug_id: str, task_id: str) -> dict:
    """Poll Celery task status for async ingestion."""
    settings = get_settings()
    if settings.ingest_mode != "async":
        return {"status": "sync_mode", "message": "Async mode not enabled."}

    from app.tasks.ingest import celery_app
    result = celery_app.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "drug_id": drug_id,
        "state": result.state,
        "result": result.result if result.ready() else None,
    }
