"""
Block 7 — Scoring validation & calibration harness.

Deterministic. No external I/O. Uses existing scoring + vectors to compute
health metrics and recommendations.
"""
from __future__ import annotations

from statistics import mean, pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MechanismVector
from app.routes.vectorize import _load_disease_short
from app.services.drug_summary_builder import build_drug_short
from app.services.evidence_ledger import build_pair_evidence
from app.services.mechanism_vocab import MECH_NODES_HASH, MECH_VOCAB_VERSION
from app.services.scoring import DEFAULT_WEIGHTS, score_pair


def _safe_stats(values: list[float]) -> tuple[float, float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0, 0.0
    mn = min(values)
    mx = max(values)
    mu = mean(values)
    sd = pstdev(values) if len(values) > 1 else 0.0
    return mn, mx, mu, sd


DATA_SUFFICIENCY_KEYS = frozenset({
    "vectorized_drugs_count", "vectorized_diseases_count",
    "drugs_missing_short_summary", "diseases_missing_short_summary",
    "drugs_empty_sparse", "diseases_empty_sparse",
    "diseases_no_genes_count",
    "evidence_recompute_succeeded", "evidence_recompute_failed",
})


def format_data_health(counts: dict[str, int]) -> dict[str, int]:
    """
    Format data sufficiency counts for API response. Pure, no DB.
    Ensures all expected keys are present with int values.
    """
    return {k: int(counts.get(k, 0)) for k in sorted(DATA_SUFFICIENCY_KEYS)}


def _is_empty_sparse(sparse: dict | None) -> bool:
    """True if sparse is empty or has no positive weights."""
    if not sparse:
        return True
    return not any(
        float(v.get("weight", 0) or 0) > 0
        for v in sparse.values()
        if isinstance(v, dict)
    )


def compute_data_sufficiency(
    db: Session,
    drug_mvs: list,
    disease_mvs: list,
    best_disease_per_drug: dict[str, str] | None = None,
) -> dict[str, int]:
    """
    Compute data sufficiency metrics from vectorized drugs/diseases.
    Fast, deterministic. No external I/O.
    """
    best = best_disease_per_drug or {}
    vectorized_drugs_count = len(drug_mvs)
    vectorized_diseases_count = len(disease_mvs)
    drugs_missing_short_summary = 0
    diseases_missing_short_summary = 0
    drugs_empty_sparse = 0
    diseases_empty_sparse = 0
    diseases_no_genes_count = 0
    evidence_recompute_succeeded = 0
    evidence_recompute_failed = 0

    for dmv in drug_mvs:
        drug_id = dmv.entity_id
        short = build_drug_short(drug_id, db)
        if short is None:
            drugs_missing_short_summary += 1
        sparse = dmv.sparse or {}
        if _is_empty_sparse(sparse):
            drugs_empty_sparse += 1

    for dvm in disease_mvs:
        disease_id = dvm.entity_id
        short = _load_disease_short(disease_id, db)
        if short is None:
            diseases_missing_short_summary += 1
        else:
            genes = short.get("genes") or []
            if not (isinstance(genes, list) and len(genes) > 0):
                diseases_no_genes_count += 1
        sparse = dvm.sparse or {}
        if _is_empty_sparse(sparse):
            diseases_empty_sparse += 1

    for drug_id, disease_id in best.items():
        dmv = next((m for m in drug_mvs if m.entity_id == drug_id), None)
        dvm = next((m for m in disease_mvs if m.entity_id == disease_id), None)
        if dmv is None or dvm is None:
            evidence_recompute_failed += 1
            continue
        drug_short = build_drug_short(drug_id, db) or {}
        disease_short = _load_disease_short(disease_id, db)
        if disease_short is None:
            evidence_recompute_failed += 1
            continue
        try:
            out = score_pair(
                drug_short,
                disease_short,
                dmv.dense_weights or [],
                dvm.dense_weights or [],
                dmv.sparse or {},
                dvm.sparse or {},
            )
            build_pair_evidence(
                drug_short,
                disease_short,
                dmv.sparse or {},
                dvm.sparse or {},
                out["breakdown"],
                drug_id=drug_id,
                disease_id=disease_id,
            )
            evidence_recompute_succeeded += 1
        except Exception:
            evidence_recompute_failed += 1

    return {
        "vectorized_drugs_count": vectorized_drugs_count,
        "vectorized_diseases_count": vectorized_diseases_count,
        "drugs_missing_short_summary": drugs_missing_short_summary,
        "diseases_missing_short_summary": diseases_missing_short_summary,
        "drugs_empty_sparse": drugs_empty_sparse,
        "diseases_empty_sparse": diseases_empty_sparse,
        "diseases_no_genes_count": diseases_no_genes_count,
        "evidence_recompute_succeeded": evidence_recompute_succeeded,
        "evidence_recompute_failed": evidence_recompute_failed,
    }


def compute_recommendations(global_metrics: dict[str, Any]) -> list[str]:
    """
    Pure function: derive human-readable recommendations from metrics.
    Used both by validate_scoring_system and tests (with synthetic data).
    """
    recs: list[str] = []

    score_std = float(global_metrics.get("score_std", 0.0))
    percent_zero = float(global_metrics.get("percent_zero_scores", 0.0))
    percent_negative = float(global_metrics.get("percent_negative_scores", 0.0))
    direction_effect = float(global_metrics.get("direction_weight_effect", 0.0))
    mech_mean = float(global_metrics.get("mechanism_score_mean", 0.0))

    if score_std < 0.02:
        recs.append("Score collapse detected: consider increasing mechanism weight or reducing penalties.")

    if percent_zero > 40.0:
        recs.append("Excessive zero scores: consider clamping minimum demo score (e.g., 0.01) or lowering thresholds.")

    if direction_effect > mech_mean and mech_mean > 0:
        recs.append("Direction overpowering similarity: reduce direction_weight to around 0.3–0.4.")

    if percent_negative > 30.0:
        recs.append("Excessive negative scoring: reduce uncertainty_weight or safety_weight slightly.")

    if not recs:
        recs.append("Scoring health looks acceptable for demo use.")

    return recs


def validate_scoring_system(db: Session) -> dict[str, Any]:
    """
    Run scoring across all vectorized drug–disease pairs and compute health metrics.

    Returns:
      {
        "global_metrics": {...},
        "per_drug_metrics": {...},
        "recommendations": [...],
      }
    """
    # 1) Collect vectors
    drug_mvs: list[MechanismVector] = db.execute(
        select(MechanismVector).where(
            MechanismVector.entity_type == "drug",
            MechanismVector.vocab_version == MECH_VOCAB_VERSION,
            MechanismVector.nodes_hash == MECH_NODES_HASH,
        )
    ).scalars().all()

    disease_mvs: list[MechanismVector] = db.execute(
        select(MechanismVector).where(
            MechanismVector.entity_type == "disease",
            MechanismVector.vocab_version == MECH_VOCAB_VERSION,
            MechanismVector.nodes_hash == MECH_NODES_HASH,
        )
    ).scalars().all()

    if not drug_mvs or not disease_mvs:
        data_suff = compute_data_sufficiency(db, drug_mvs, disease_mvs, {})
        return {
            "global_metrics": {
                "score_min": 0.0,
                "score_max": 0.0,
                "score_mean": 0.0,
                "score_std": 0.0,
                "percent_negative_scores": 0.0,
                "percent_zero_scores": 0.0,
                "percent_direction_contributing": 0.0,
                "percent_scores_with_safety_penalty": 0.0,
                "percent_scores_with_uncertainty_penalty": 0.0,
                "mechanism_score_mean": 0.0,
                "direction_weight_effect": 0.0,
                "average_top5_minus_median_gap": 0.0,
            },
            "per_drug_metrics": {},
            "recommendations": [
                "No vectorized drugs or diseases found; ingest and vectorize entities before validation."
            ],
            "data_sufficiency": data_suff,
        }

    all_scores: list[float] = []
    all_mech_scores: list[float] = []
    all_direction_contrib: list[float] = []
    dir_nonzero_count = 0
    safety_penalty_count = 0
    uncertainty_penalty_count = 0

    per_drug_scores: dict[str, list[float]] = {}
    best_disease_per_drug: dict[str, str] = {}

    for dmv in drug_mvs:
        drug_id = dmv.entity_id
        drug_dense = dmv.dense_weights or []
        drug_sparse = dmv.sparse or {}
        drug_short = build_drug_short(drug_id, db) or {}

        scores_for_drug: list[tuple[float, str]] = []

        for dis_mv in disease_mvs:
            disease_id = dis_mv.entity_id
            disease_dense = dis_mv.dense_weights or []
            disease_sparse = dis_mv.sparse or {}
            disease_short = _load_disease_short(disease_id, db)
            if disease_short is None:
                continue

            out = score_pair(
                drug_short,
                disease_short,
                drug_dense,
                disease_dense,
                drug_sparse,
                disease_sparse,
            )
            score = float(out["final_score"])
            mech_score = float(out["breakdown"]["mechanism"]["score"])
            direction_score = float(out["breakdown"]["direction"]["direction_score"])
            safety_penalty_val = float(out["breakdown"]["safety"]["penalty"])
            uncertainty_penalty_val = float(out["breakdown"]["uncertainty"]["penalty"])

            all_scores.append(score)
            all_mech_scores.append(mech_score)
            scores_for_drug.append((score, disease_id))

            dir_contrib = abs(DEFAULT_WEIGHTS["direction"] * direction_score)
            all_direction_contrib.append(dir_contrib)
            if abs(direction_score) > 0.01:
                dir_nonzero_count += 1

            if safety_penalty_val > 0:
                safety_penalty_count += 1
            if uncertainty_penalty_val > 0:
                uncertainty_penalty_count += 1

        if scores_for_drug:
            per_drug_scores[drug_id] = [s[0] for s in scores_for_drug]
            best = max(scores_for_drug, key=lambda s: s[0])
            best_disease_per_drug[drug_id] = best[1]

    n = len(all_scores)
    if n == 0:
        data_suff = compute_data_sufficiency(db, drug_mvs, disease_mvs, best_disease_per_drug)
        return {
            "global_metrics": {
                "score_min": 0.0,
                "score_max": 0.0,
                "score_mean": 0.0,
                "score_std": 0.0,
                "percent_negative_scores": 0.0,
                "percent_zero_scores": 0.0,
                "percent_direction_contributing": 0.0,
                "percent_scores_with_safety_penalty": 0.0,
                "percent_scores_with_uncertainty_penalty": 0.0,
                "mechanism_score_mean": 0.0,
                "direction_weight_effect": 0.0,
                "average_top5_minus_median_gap": 0.0,
            },
            "per_drug_metrics": {},
            "recommendations": [
                "No scored drug–disease pairs produced; ensure vectors and summaries exist."
            ],
            "data_sufficiency": data_suff,
        }

    score_min, score_max, score_mean, score_std = _safe_stats(all_scores)
    mech_min, mech_max, mech_mean, _ = _safe_stats(all_mech_scores)

    negative_count = sum(1 for s in all_scores if s < 0)
    zero_count = sum(1 for s in all_scores if abs(s) < 1e-9)
    percent_negative = 100.0 * negative_count / n
    percent_zero = 100.0 * zero_count / n

    percent_dir_contrib = 100.0 * dir_nonzero_count / n if n else 0.0
    percent_safety = 100.0 * safety_penalty_count / n if n else 0.0
    percent_uncertainty = 100.0 * uncertainty_penalty_count / n if n else 0.0
    direction_weight_effect = mean(all_direction_contrib) if all_direction_contrib else 0.0

    # Per-drug metrics
    per_drug_metrics: dict[str, Any] = {}
    gaps: list[float] = []
    for drug_id, scores in per_drug_scores.items():
        mn, mx, mu, sd = _safe_stats(scores)
        rng = mx - mn
        sorted_scores = sorted(scores, reverse=True)
        if len(sorted_scores) >= 5:
            top5 = sorted_scores[:5]
            median_idx = len(sorted_scores) // 2
            median_val = sorted_scores[median_idx]
            gap = mean(top5) - median_val
            gaps.append(gap)
        else:
            gap = 0.0
        per_drug_metrics[drug_id] = {
            "score_min": mn,
            "score_max": mx,
            "score_mean": mu,
            "score_std": sd,
            "score_range": rng,
            "top5_minus_median_gap": gap,
        }

    avg_gap = mean(gaps) if gaps else 0.0

    global_metrics = {
        "score_min": score_min,
        "score_max": score_max,
        "score_mean": score_mean,
        "score_std": score_std,
        "percent_negative_scores": percent_negative,
        "percent_zero_scores": percent_zero,
        "percent_direction_contributing": percent_dir_contrib,
        "percent_scores_with_safety_penalty": percent_safety,
        "percent_scores_with_uncertainty_penalty": percent_uncertainty,
        "mechanism_score_mean": mech_mean,
        "direction_weight_effect": direction_weight_effect,
        "average_top5_minus_median_gap": avg_gap,
    }

    recommendations = compute_recommendations(global_metrics)
    data_sufficiency = compute_data_sufficiency(db, drug_mvs, disease_mvs, best_disease_per_drug)

    return {
        "global_metrics": global_metrics,
        "per_drug_metrics": per_drug_metrics,
        "recommendations": recommendations,
        "data_sufficiency": data_sufficiency,
    }

