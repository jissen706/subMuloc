"""
Toxicity interpreter (post-processing, DB read-only input).

Reads from:
  trial, label_warning, adverse_event

Writes to:
  toxicity_metric, evidence(tox)

Heuristics (explicit, no ML):
  - Phase 1 completed with no safety stop  → provisional_safe
  - Any trial terminated for toxicity keywords → trial_termination_safety_concern
  - Boxed warning exists                    → boxed_warning_flag
  - Contraindication exists                 → contraindication_flag
  - SAE mentioned in trial results          → sae_flag
  - FAERS high-count events (≥50 reports)  → faers_signal
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import AdverseEvent, Evidence, LabelWarning, ToxicityMetric, Trial

logger = logging.getLogger(__name__)

TOXICITY_STOP_KEYWORDS = {
    "toxicity", "toxic", "adverse", "safety concern", "safety issue",
    "terminated for safety", "dose-limiting", "dlt", "overdose",
    "drug-related death", "fatal", "serious adverse",
}

SAE_KEYWORDS = {
    "serious adverse event", "sae", "life-threatening", "hospitali",
    "disability", "death", "fatal outcome",
}


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _emit_metric(
    session: Session,
    drug_id: str,
    metric_type: str,
    value: str | None,
    units: str | None,
    interpreted_flag: str,
    evidence_source: str,
    evidence_ref: str | None,
    notes: str,
) -> None:
    session.add(ToxicityMetric(
        id=_new_uuid(),
        drug_id=drug_id,
        metric_type=metric_type,
        value=value,
        units=units,
        interpreted_flag=interpreted_flag,
        evidence_source=evidence_source,
        evidence_ref=evidence_ref,
        notes=notes,
    ))


def _emit_evidence(
    session: Session,
    drug_id: str,
    title: str,
    snippet: str,
    source: str,
    url: str | None = None,
    meta: dict | None = None,
) -> None:
    session.add(Evidence(
        id=_new_uuid(),
        drug_id=drug_id,
        source=source,
        evidence_type="tox",
        title=title,
        snippet_text=snippet[:512] if snippet else None,
        url=url,
        metadata_json=meta,
    ))


def run(session: Session, drug_id: str) -> int:
    """
    Run all tox heuristics for the given drug_id.
    Returns the number of toxicity_metric rows created.
    """
    # Wipe any previous run's output so re-ingesting is idempotent.
    session.execute(delete(ToxicityMetric).where(ToxicityMetric.drug_id == drug_id))
    count = 0

    # -----------------------------------------------------------------------
    # 1. Trial-based heuristics
    # -----------------------------------------------------------------------
    trials = session.execute(
        select(Trial).where(Trial.drug_id == drug_id)
    ).scalars().all()

    phase1_completed = False
    has_terminated_safety = False
    has_sae = False

    for trial in trials:
        status_lower = (trial.status or "").lower()
        title_lower = (trial.title or "").lower()
        phase_lower = (trial.phase or "").lower()

        # Check for terminated-for-safety
        if "terminat" in status_lower:
            raw = trial.raw_json or {}
            why_stopped_text = ""
            # Try to find why-stopped info in raw JSON
            if isinstance(raw, dict):
                ps = raw.get("protocolSection", {})
                why_stopped = (
                    ps.get("statusModule", {}).get("whyStopped", "")
                    or ps.get("oversightModule", {}).get("isFdaRegulatedDrug", "")
                )
                why_stopped_text = str(why_stopped).lower()

            combined = title_lower + " " + why_stopped_text
            if any(kw in combined for kw in TOXICITY_STOP_KEYWORDS):
                has_terminated_safety = True
                _emit_metric(
                    session, drug_id,
                    metric_type="DLT_flag",
                    value="terminated_for_safety",
                    units=None,
                    interpreted_flag="concerning",
                    evidence_source="ctgov",
                    evidence_ref=trial.nct_id,
                    notes=f"Trial {trial.nct_id} terminated; safety signal detected in title/status.",
                )
                _emit_evidence(
                    session, drug_id,
                    title=f"Trial terminated for safety: {trial.nct_id}",
                    snippet=f"Status: {trial.status} | Title: {trial.title}",
                    source="ctgov",
                    url=trial.url,
                    meta={"nct_id": trial.nct_id, "status": trial.status},
                )
                count += 1

        # Check for phase 1 completion
        if "phase 1" in phase_lower or "phase1" in phase_lower:
            if "complet" in status_lower:
                phase1_completed = True

        # Check for SAE mentions in raw JSON
        if trial.raw_json and isinstance(trial.raw_json, dict):
            raw_str = str(trial.raw_json).lower()
            if any(kw in raw_str for kw in SAE_KEYWORDS):
                has_sae = True

    if phase1_completed and not has_terminated_safety:
        _emit_metric(
            session, drug_id,
            metric_type="SAE_rate",
            value="phase1_completed",
            units=None,
            interpreted_flag="safe",
            evidence_source="ctgov",
            evidence_ref=None,
            notes="Phase 1 trial completed without detected safety termination (provisional).",
        )
        _emit_evidence(
            session, drug_id,
            title="Phase 1 completion: provisional safety",
            snippet="At least one Phase 1 trial completed without safety-related termination.",
            source="ctgov",
        )
        count += 1

    if has_sae:
        _emit_metric(
            session, drug_id,
            metric_type="SAE_rate",
            value="sae_mentioned",
            units=None,
            interpreted_flag="concerning",
            evidence_source="ctgov",
            evidence_ref=None,
            notes="Serious adverse event language detected in trial data.",
        )
        count += 1

    # -----------------------------------------------------------------------
    # 2. Label warning heuristics
    # -----------------------------------------------------------------------
    warnings = session.execute(
        select(LabelWarning).where(LabelWarning.drug_id == drug_id)
    ).scalars().all()

    has_boxed = False
    has_contraindication = False

    for w in warnings:
        section = (w.section or "").lower()
        if "boxed" in section:
            has_boxed = True
        if "contraindic" in section:
            has_contraindication = True

    if has_boxed:
        _emit_metric(
            session, drug_id,
            metric_type="DLT_flag",
            value="boxed_warning",
            units=None,
            interpreted_flag="concerning",
            evidence_source="openfda",
            evidence_ref="boxed_warning",
            notes="FDA boxed warning exists on drug label.",
        )
        _emit_evidence(
            session, drug_id,
            title="Boxed warning on FDA label",
            snippet="Drug has an FDA boxed (black box) warning.",
            source="openfda",
        )
        count += 1

    if has_contraindication:
        _emit_metric(
            session, drug_id,
            metric_type="DLT_flag",
            value="contraindication",
            units=None,
            interpreted_flag="concerning",
            evidence_source="openfda",
            evidence_ref="contraindications",
            notes="Contraindication section present on FDA label.",
        )
        count += 1

    # -----------------------------------------------------------------------
    # 3. FAERS adverse event heuristics
    # -----------------------------------------------------------------------
    faers_events = session.execute(
        select(AdverseEvent).where(
            AdverseEvent.drug_id == drug_id,
            AdverseEvent.source == "openfda",
        )
    ).scalars().all()

    high_signal_events = []
    for ae in faers_events:
        freq = ae.frequency
        try:
            freq_int = int(float(freq)) if freq else 0
        except (ValueError, TypeError):
            freq_int = 0
        if freq_int >= 50 or ae.seriousness == "high":
            high_signal_events.append(ae)

    if high_signal_events:
        _emit_metric(
            session, drug_id,
            metric_type="SAE_rate",
            value=str(len(high_signal_events)),
            units="high_signal_events",
            interpreted_flag="concerning",
            evidence_source="openfda",
            evidence_ref="faers",
            notes=f"{len(high_signal_events)} high-signal FAERS adverse events detected.",
        )
        _emit_evidence(
            session, drug_id,
            title="FAERS high-signal adverse events",
            snippet=f"{len(high_signal_events)} events with ≥50 reports or high seriousness in FAERS.",
            source="openfda",
            meta={"count": len(high_signal_events)},
        )
        count += 1

    if count == 0:
        # No specific signals → unknown
        _emit_metric(
            session, drug_id,
            metric_type="SAE_rate",
            value=None,
            units=None,
            interpreted_flag="unknown",
            evidence_source="ctgov",
            evidence_ref=None,
            notes="Insufficient data to determine toxicity profile.",
        )
        count += 1

    session.commit()
    logger.info("tox_interpreter drug_id=%s metrics_created=%d", drug_id, count)
    return count
