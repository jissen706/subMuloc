"""
Block 3 — Scoring engine: rank diseases for a given drug.

Deterministic. Uses mechanism similarity, safety penalty, evidence score, uncertainty penalty.
All components normalized 0..1.
"""
from __future__ import annotations

from typing import Any

from app.services.mechanism_mapper import cosine_similarity
from app.services.mechanism_vocab import MECH_NODES


DEFAULT_WEIGHTS = {
    "mechanism": 1.0,
    "evidence": 0.4,
    "safety": 0.8,
    "uncertainty": 0.3,
}


def mechanism_score(
    drug_dense: list[float],
    disease_dense: list[float],
) -> dict[str, Any]:
    """
    Compute cosine similarity and top 5 overlapping nodes (min(drug_w, disease_w)).
    Returns score in [0, 1] and top_nodes list.
    """
    score = max(0.0, min(1.0, cosine_similarity(drug_dense or [], disease_dense or [])))
    overlap: list[tuple[str, float, float]] = []
    d_drug = drug_dense or []
    d_disease = disease_dense or []
    for i, node in enumerate(MECH_NODES):
        if i >= len(d_drug) or i >= len(d_disease):
            continue
        w_d = float(d_drug[i]) if i < len(d_drug) else 0.0
        w_dis = float(d_disease[i]) if i < len(d_disease) else 0.0
        if w_d > 0 and w_dis > 0:
            overlap.append((node, w_d, w_dis))
    overlap.sort(key=lambda x: (-min(x[1], x[2]), x[0]))
    top_nodes = [
        {"node": n, "drug_w": round(dw, 4), "disease_w": round(disw, 4)}
        for n, dw, disw in overlap[:5]
    ]
    return {"score": round(score, 6), "top_nodes": top_nodes}


def safety_penalty(drug_short: dict) -> dict[str, Any]:
    """
    Build safety penalty from boxed warning, terminated trials, toxicity flags, black-box language.
    Defensive about field names. Cap penalty at 1.0.
    """
    penalty = 0.0
    reasons: list[str] = []

    if not drug_short or not isinstance(drug_short, dict):
        return {"penalty": 0.0, "reasons": []}

    # Boxed warning
    safety = drug_short.get("safety") or {}
    if isinstance(safety, dict) and safety.get("boxed_warning"):
        penalty += 0.4
        reasons.append("boxed_warning")

    # Label warnings: check for boxed_warning section if not in safety
    label_warnings = drug_short.get("label_warnings") or []
    if isinstance(label_warnings, list) and not reasons:
        for w in label_warnings:
            if isinstance(w, dict) and (w.get("section") or "").strip().lower() == "boxed_warning":
                penalty += 0.4
                reasons.append("boxed_warning")
                break

    # Terminated trials (>3)
    trials = drug_short.get("trials") or {}
    if isinstance(trials, dict):
        by_status = trials.get("by_status") or trials.get("status_counts") or {}
        term_count = 0
        for k, v in (by_status if isinstance(by_status, dict) else {}).items():
            if k and str(k).upper() in ("TERMINATED", "WITHDRAWN", "SUSPENDED"):
                term_count += int(v) if isinstance(v, (int, float)) else 0
        if term_count > 3:
            penalty += 0.2
            reasons.append("many_terminated_trials")
        notables = trials.get("notables") or trials.get("trials") or []
        if isinstance(notables, list) and term_count <= 3:
            term_count = sum(
                1 for t in notables
                if isinstance(t, dict) and str(t.get("status") or "").upper() in ("TERMINATED", "WITHDRAWN", "SUSPENDED")
            )
            if term_count > 3:
                penalty += 0.2
                reasons.append("many_terminated_trials")

    # Serious toxicity flags (any present)
    tox_flags = safety.get("toxicity_flags") or drug_short.get("toxicity_flags") or []
    if isinstance(tox_flags, list) and len(tox_flags) > 0:
        penalty += 0.2
        reasons.append("serious_toxicity_flags")

    # Black-box style language in notes
    notes = drug_short.get("notes") or []
    if isinstance(notes, list):
        for n in notes:
            s = (n or "").lower()
            if "boxed" in s or "black" in s or "warning" in s:
                if "black-box" not in reasons:
                    penalty += 0.2
                    reasons.append("black_box_style_language")
                break

    penalty = min(1.0, penalty)
    return {"penalty": round(penalty, 4), "reasons": reasons}


def evidence_score(drug_short: dict, disease_short: dict) -> dict[str, Any]:
    """
    Human exposure proxy (trials) + literature proxy (pubs). Cap at 1.0.
    """
    score = 0.0
    reasons: list[str] = []

    if not isinstance(drug_short, dict):
        drug_short = {}
    if not isinstance(disease_short, dict):
        disease_short = {}

    # Drug trials total
    trials = drug_short.get("trials") or {}
    total_trials = int(trials.get("total", 0)) if isinstance(trials, dict) else 0
    if total_trials >= 10:
        score += 0.4
        reasons.append("drug_trials_10_plus")
    elif total_trials >= 3:
        score += 0.2
        reasons.append("drug_trials_3_plus")

    # Disease pubs_total
    stats = disease_short.get("stats") or {}
    pubs_total = int(stats.get("pubs_total", 0)) if isinstance(stats, dict) else 0
    if pubs_total >= 200:
        score += 0.3
        reasons.append("disease_pubs_200_plus")
    elif pubs_total >= 50:
        score += 0.15
        reasons.append("disease_pubs_50_plus")

    # Drug publications boost
    drug_stats = drug_short.get("stats") or {}
    drug_pubs = int(drug_stats.get("pubs_total", 0)) if isinstance(drug_stats, dict) else 0
    if drug_pubs >= 100:
        score += 0.1
        reasons.append("drug_pubs_high")
    elif drug_pubs >= 50:
        score += 0.05
        reasons.append("drug_pubs_moderate")

    score = min(1.0, score)
    return {"score": round(score, 4), "reasons": reasons}


def uncertainty_penalty(
    drug_short: dict,
    disease_short: dict,
    drug_sparse: dict,
    disease_sparse: dict,
) -> dict[str, Any]:
    """
    Penalize empty vectors, no genes, low pubs. Cap at 1.0.
    """
    penalty = 0.0
    reasons: list[str] = []

    drug_sparse = drug_sparse or {}
    disease_sparse = disease_sparse or {}
    disease_short = disease_short or {}
    drug_short = drug_short or {}

    if not drug_sparse or not any(
        (v.get("weight") or 0) > 0 for v in drug_sparse.values() if isinstance(v, dict)
    ):
        penalty += 0.4
        reasons.append("drug_vector_empty")

    if not disease_sparse or not any(
        (v.get("weight") or 0) > 0 for v in disease_sparse.values() if isinstance(v, dict)
    ):
        penalty += 0.4
        reasons.append("disease_vector_empty")

    genes = disease_short.get("genes") or []
    if not (isinstance(genes, list) and len(genes) > 0):
        penalty += 0.2
        reasons.append("disease_no_genes")

    stats = disease_short.get("stats") or {}
    pubs_total = int(stats.get("pubs_total", 0)) if isinstance(stats, dict) else 0
    if pubs_total < 10:
        penalty += 0.2
        reasons.append("disease_pubs_low")

    penalty = min(1.0, penalty)
    return {"penalty": round(penalty, 4), "reasons": reasons}


def score_pair(
    drug_short: dict,
    disease_short: dict,
    drug_dense: list[float],
    disease_dense: list[float],
    drug_sparse: dict,
    disease_sparse: dict,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Combine mechanism, evidence, safety, uncertainty with weights.
    Returns final_score and full breakdown. Deterministic.
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}

    mech = mechanism_score(drug_dense or [], disease_dense or [])
    ev = evidence_score(drug_short or {}, disease_short or {})
    safe = safety_penalty(drug_short or {})
    unc = uncertainty_penalty(
        drug_short or {},
        disease_short or {},
        drug_sparse or {},
        disease_sparse or {},
    )

    final = (
        w.get("mechanism", 1.0) * mech["score"]
        + w.get("evidence", 0.4) * ev["score"]
        - w.get("safety", 0.8) * safe["penalty"]
        - w.get("uncertainty", 0.3) * unc["penalty"]
    )
    final = round(final, 6)

    return {
        "final_score": final,
        "breakdown": {
            "mechanism": mech,
            "evidence": ev,
            "safety": safe,
            "uncertainty": unc,
        },
    }
