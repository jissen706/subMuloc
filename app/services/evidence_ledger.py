"""
Block 4 — Evidence ledger for drug-disease pairs.

Deterministic, lightweight, safe. Builds structured evidence from scoring breakdown
and short summaries. No external API calls.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models import PairEvidence
from app.services.mechanism_vocab import MECH_ALIASES

SCORE_VERSION = "score_v1"
EVIDENCE_VERSION = "evidence_v1"

_MAX_MECHANISM_OVERLAP = 5
_MAX_PATHWAY_TRIGGERS = 5
_MAX_SAFETY_FLAGS = 5
_MAX_TERMS_PER_NODE = 3


def _term_matches_node(term: str, node: str) -> bool:
    """True if term (lowercase) contains node or any node alias."""
    if not term:
        return False
    t = term.lower()
    if node.lower() in t:
        return True
    for alias in MECH_ALIASES.get(node, []):
        if alias and alias in t:
            return True
    return False


def _pathway_terms_for_node(
    pathways_top: list,
    node: str,
    max_terms: int = _MAX_TERMS_PER_NODE,
) -> list[str]:
    """Extract matching pathway terms for a node. Returns ['term (count)', ...]."""
    out: list[str] = []
    if not isinstance(pathways_top, list):
        return out
    for p in pathways_top:
        if not isinstance(p, dict):
            continue
        term = (p.get("term") or "").strip()
        count = int(p.get("count") or 0)
        if term and _term_matches_node(term, node):
            out.append(f"{term} ({count})")
            if len(out) >= max_terms:
                break
    return out


def build_pair_evidence(
    drug_short: dict,
    disease_short: dict,
    drug_sparse: dict,
    disease_sparse: dict,
    scoring_breakdown: dict,
    drug_id: str = "",
    disease_id: str = "",
) -> dict:
    """
    Build structured evidence payload from scoring breakdown and short summaries.
    Deterministic, defensive. Caps list lengths.
    """
    drug_short = drug_short or {}
    disease_short = disease_short or {}
    drug_sparse = drug_sparse or {}
    disease_sparse = disease_sparse or {}
    scoring_breakdown = scoring_breakdown or {}

    # 1) Mechanism overlap from breakdown
    mech = scoring_breakdown.get("mechanism") or {}
    top_nodes_raw = mech.get("top_nodes") or []
    mechanism_overlap: list[dict] = []
    for n in top_nodes_raw[: _MAX_MECHANISM_OVERLAP]:
        if not isinstance(n, dict):
            continue
        node = (n.get("node") or "").strip()
        drug_w = float(n.get("drug_w") or 0)
        disease_w = float(n.get("disease_w") or 0)
        if node:
            overlap_weight = min(drug_w, disease_w)
            mechanism_overlap.append({
                "node": node,
                "drug_weight": round(drug_w, 4),
                "disease_weight": round(disease_w, 4),
                "overlap_weight": round(overlap_weight, 4),
            })
    mechanism_overlap.sort(key=lambda x: (-x["overlap_weight"], x["node"]))
    mechanism_overlap = mechanism_overlap[:_MAX_MECHANISM_OVERLAP]

    # 2) Pathway triggers for overlapping nodes
    drug_pathways = drug_short.get("pathways_top") or []
    disease_pathways = disease_short.get("pathways_top") or []
    pathway_triggers: list[dict] = []
    seen_nodes: set[str] = set()
    for m in mechanism_overlap:
        node = m.get("node") or ""
        if not node or node in seen_nodes:
            continue
        seen_nodes.add(node)
        drug_terms = _pathway_terms_for_node(drug_pathways, node)
        disease_terms = _pathway_terms_for_node(disease_pathways, node)
        if drug_terms or disease_terms:
            pathway_triggers.append({
                "node": node,
                "drug_terms": drug_terms,
                "disease_terms": disease_terms,
            })
        if len(pathway_triggers) >= _MAX_PATHWAY_TRIGGERS:
            break

    # 3) Safety flags from drug_short
    safety_flags: list[dict] = []
    safety = drug_short.get("safety") or {}
    if isinstance(safety, dict) and safety.get("boxed_warning"):
        safety_flags.append({"type": "boxed_warning", "detail": "Present"})
    trials = drug_short.get("trials") or {}
    if isinstance(trials, dict):
        by_status = trials.get("by_status") or trials.get("status_counts") or {}
        term_count = 0
        for k, v in (by_status if isinstance(by_status, dict) else {}).items():
            if k and str(k).upper() in ("TERMINATED", "WITHDRAWN", "SUSPENDED"):
                term_count += int(v) if isinstance(v, (int, float)) else 0
        notables = trials.get("notables") or []
        if isinstance(notables, list) and term_count == 0:
            for t in notables:
                if isinstance(t, dict) and str(t.get("status") or "").upper() in ("TERMINATED", "WITHDRAWN", "SUSPENDED"):
                    term_count += 1
        if term_count > 0:
            safety_flags.append({"type": "terminated_trials", "count": term_count})
    tox_flags = safety.get("toxicity_flags") or drug_short.get("toxicity_flags") or []
    if isinstance(tox_flags, list) and len(tox_flags) > 0:
        safety_flags.append({"type": "toxicity_flags", "count": len(tox_flags)})
    safety_flags = safety_flags[:_MAX_SAFETY_FLAGS]

    # 4) Trial summary
    total_trials = int(trials.get("total") or 0) if isinstance(trials, dict) else 0
    terminated_trials = 0
    if isinstance(trials, dict):
        by_status = trials.get("by_status") or trials.get("status_counts") or {}
        for k, v in (by_status if isinstance(by_status, dict) else {}).items():
            if k and str(k).upper() in ("TERMINATED", "WITHDRAWN", "SUSPENDED"):
                terminated_trials += int(v) if isinstance(v, (int, float)) else 0
    trial_summary = {
        "total_trials": total_trials,
        "terminated_trials": terminated_trials,
    }

    # 5) Literature summary
    disease_stats = disease_short.get("stats") or {}
    drug_stats = drug_short.get("stats") or {}
    literature_summary: dict[str, Any] = {
        "disease_pubs_total": int(disease_stats.get("pubs_total") or 0) if isinstance(disease_stats, dict) else 0,
        "drug_pubs_total": int(drug_stats.get("pubs_total") or 0) if isinstance(drug_stats, dict) else 0,
    }

    return {
        "drug_id": drug_id or "",
        "disease_id": disease_id or "",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mechanism_overlap": mechanism_overlap,
        "pathway_triggers": pathway_triggers,
        "safety_flags": safety_flags,
        "trial_summary": trial_summary,
        "literature_summary": literature_summary,
        "version": EVIDENCE_VERSION,
    }


def store_pair_evidence(
    db,
    drug_id: str,
    disease_id: str,
    payload: dict,
    score_version: str = SCORE_VERSION,
) -> PairEvidence:
    """Upsert PairEvidence by (drug_id, disease_id, score_version). Returns row."""
    existing = db.execute(
        select(PairEvidence).where(
            PairEvidence.drug_id == drug_id,
            PairEvidence.disease_id == disease_id,
            PairEvidence.score_version == score_version,
        )
    ).scalars().first()

    if existing is not None:
        existing.payload = payload
        db.flush()
        db.refresh(existing)
        return existing

    row = PairEvidence(
        drug_id=drug_id,
        disease_id=disease_id,
        score_version=score_version,
        payload=payload,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get_pair_evidence(
    db,
    drug_id: str,
    disease_id: str,
    score_version: str = SCORE_VERSION,
) -> dict | None:
    """Return payload if exists, else None."""
    row = db.execute(
        select(PairEvidence).where(
            PairEvidence.drug_id == drug_id,
            PairEvidence.disease_id == disease_id,
            PairEvidence.score_version == score_version,
        )
    ).scalars().first()
    if row is None:
        return None
    return row.payload
