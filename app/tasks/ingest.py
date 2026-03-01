"""
Celery ingestion task + pipeline registry.

Adding a new source = (1) create app/services/<source>.py, (2) add one line here.

Pipeline execution order matters:
  PubChem first  → populates molecular structure + pubchem_cid
  ChEMBL second  → populates targets (gene symbols needed by ClinVar)
  CTGov/PubMed   → independent, can run in any order
  OpenFDA        → independent
  ClinVar last   → reads gene symbols from target table
"""
from __future__ import annotations

import logging

from celery import Celery

from app.config import get_settings
from app.db import SessionLocal
from app.postprocess import pathway_extractor, tox_interpreter
from app.services.base import DrugContext
from app.services.chembl import ChEMBLIngestor
from app.services.clinvar import ClinVarIngestor
from app.services.ctgov import CTGovIngestor
from app.services.openfda import OpenFDAIngestor
from app.services.pubchem import PubChemIngestor
from app.services.pubmed import PubMedIngestor

logger = logging.getLogger(__name__)

settings = get_settings()

# ---------------------------------------------------------------------------
# Celery app
# ---------------------------------------------------------------------------
celery_app = Celery(
    "drug_intel",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# ---------------------------------------------------------------------------
# Ingestor registry — add new sources here
# ---------------------------------------------------------------------------
INGESTORS = [
    PubChemIngestor(),   # structure + identifiers
    ChEMBLIngestor(),    # targets
    CTGovIngestor(),     # trials + adverse events
    PubMedIngestor(),    # publications
    OpenFDAIngestor(),   # label warnings + FAERS
    ClinVarIngestor(),   # variant associations (needs gene symbols from ChEMBL)
]


# ---------------------------------------------------------------------------
# Core pipeline runner (sync, usable by both Celery and sync mode)
# ---------------------------------------------------------------------------
def run_pipeline(drug_id: str, ctx_dict: dict) -> dict:
    """
    Execute the full ingestion pipeline for a drug.

    Parameters
    ----------
    drug_id  : UUID string of the drug row in DB.
    ctx_dict : Serialisable snapshot of DrugContext fields.

    Returns
    -------
    dict with counts per table.
    """
    ctx = DrugContext(
        drug_id=ctx_dict["drug_id"],
        canonical_name=ctx_dict["canonical_name"],
        input_name=ctx_dict["input_name"],
        synonyms=set(ctx_dict.get("synonyms", [])),
        identifiers=ctx_dict.get("identifiers", {}),
    )

    counts: dict[str, int] = {
        "trials": 0,
        "publications": 0,
        "targets": 0,
        "label_warnings": 0,
        "adverse_events": 0,
        "clinvar_associations": 0,
        "toxicity_metrics": 0,
        "pathway_mentions": 0,
    }

    with SessionLocal() as session:
        # ----------------------------------------------------------------
        # Source ingestors
        # ----------------------------------------------------------------
        for ingestor in INGESTORS:
            try:
                records = ingestor.run(session, ctx)
                logger.info(
                    "pipeline drug=%s ingestor=%s records=%d",
                    ctx.canonical_name, ingestor.name, len(records),
                )
                # Tally by record type
                for rec in records:
                    if rec.record_type == "trial":
                        counts["trials"] += 1
                    elif rec.record_type == "publication":
                        counts["publications"] += 1
                    elif rec.record_type == "target":
                        counts["targets"] += 1
                    elif rec.record_type == "label_warning":
                        counts["label_warnings"] += 1
                    elif rec.record_type == "adverse_event":
                        counts["adverse_events"] += 1
                    elif rec.record_type == "clinvar":
                        counts["clinvar_associations"] += 1
            except Exception as exc:
                logger.error(
                    "pipeline_ingestor_error drug=%s ingestor=%s err=%s",
                    ctx.canonical_name, ingestor.name, exc, exc_info=True,
                )

        # ----------------------------------------------------------------
        # Post-processing (DB-read-only modules)
        # ----------------------------------------------------------------
        try:
            counts["toxicity_metrics"] = tox_interpreter.run(session, drug_id)
        except Exception as exc:
            logger.error("tox_interpreter_error drug=%s err=%s", drug_id, exc, exc_info=True)

        try:
            counts["pathway_mentions"] = pathway_extractor.run(session, drug_id)
        except Exception as exc:
            logger.error("pathway_extractor_error drug=%s err=%s", drug_id, exc, exc_info=True)

    logger.info("pipeline_complete drug=%s counts=%s", ctx.canonical_name, counts)
    return counts


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------
@celery_app.task(
    name="tasks.ingest.ingest_drug",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def ingest_drug(self, drug_id: str, ctx_dict: dict) -> dict:
    """Celery task wrapper around run_pipeline."""
    try:
        return run_pipeline(drug_id, ctx_dict)
    except Exception as exc:
        logger.error("celery_task_error drug_id=%s err=%s", drug_id, exc, exc_info=True)
        raise self.retry(exc=exc)
