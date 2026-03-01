"""
Block 5 — Comparator engine: similar drugs + adjacent clinical conditions.

Deterministic. No external API calls.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models import Drug, MechanismVector
from app.services.drug_summary_builder import build_drug_raw_summary, build_drug_short
from app.services.mechanism_mapper import cosine_similarity
from app.services.mechanism_vocab import MECH_NODES, MECH_NODES_HASH, MECH_VOCAB_VERSION


def _top_overlap_nodes(drug_sparse: dict, other_sparse: dict, n: int = 5) -> list[str]:
    """Return top-n shared node names ranked by min(drug_w, other_w)."""
    overlaps: list[tuple[str, float]] = []
    for node in drug_sparse:
        if node not in other_sparse:
            continue
        dw = float(drug_sparse[node].get("weight", 0.0))
        ow = float(other_sparse[node].get("weight", 0.0))
        if dw > 0.0 and ow > 0.0:
            overlaps.append((node, min(dw, ow)))
    overlaps.sort(key=lambda x: (-x[1], x[0]))
    return [node for node, _ in overlaps[:n]]


def get_similar_drugs(
    drug_id: str,
    db,
    top_k: int = 10,
) -> list[dict]:
    """
    Find mechanistically similar drugs by cosine similarity.
    Excludes self. Returns top_k with similarity, overlap nodes, trial_total, boxed_warning.
    """
    drug_mv = db.execute(
        select(MechanismVector).where(
            MechanismVector.entity_type == "drug",
            MechanismVector.entity_id == drug_id,
            MechanismVector.vocab_version == MECH_VOCAB_VERSION,
            MechanismVector.nodes_hash == MECH_NODES_HASH,
        )
    ).scalars().first()

    if drug_mv is None:
        return []

    drug_weights: list[float] = drug_mv.dense_weights or []
    drug_sparse: dict = drug_mv.sparse or {}

    if not drug_weights or len(drug_weights) != len(MECH_NODES):
        return []

    other_mvs = db.execute(
        select(MechanismVector).where(
            MechanismVector.entity_type == "drug",
            MechanismVector.entity_id != drug_id,
            MechanismVector.vocab_version == MECH_VOCAB_VERSION,
            MechanismVector.nodes_hash == MECH_NODES_HASH,
        )
    ).scalars().all()

    scored: list[dict] = []
    for omv in other_mvs:
        other_weights = omv.dense_weights or []
        if len(other_weights) != len(MECH_NODES):
            continue
        sim = cosine_similarity(drug_weights, other_weights)
        if sim <= 0.0:
            continue
        overlap_nodes = _top_overlap_nodes(drug_sparse, omv.sparse or {}, n=5)

        drug_short = build_drug_short(omv.entity_id, db) or {}
        trials = drug_short.get("trials") or {}
        trial_total = int(trials.get("total", 0)) if isinstance(trials, dict) else 0
        safety = drug_short.get("safety") or {}
        boxed_warning = bool(safety.get("boxed_warning")) if isinstance(safety, dict) else False

        drug = db.get(Drug, omv.entity_id)
        canonical_name = drug.canonical_name if drug else ""

        scored.append({
            "drug_id": omv.entity_id,
            "canonical_name": canonical_name,
            "similarity": round(sim, 6),
            "top_overlap_nodes": overlap_nodes,
            "trial_total": trial_total,
            "boxed_warning": boxed_warning,
        })

    scored.sort(key=lambda x: -x["similarity"])
    return scored[:top_k]


def get_adjacent_conditions(
    similar_drugs: list[dict],
    db,
    top_k: int = 15,
) -> list[dict]:
    """
    Aggregate trial conditions from similar drugs.
    Normalize, count frequency, return top_k.
    """
    condition_counts: dict[str, int] = {}

    for drug_info in similar_drugs:
        drug_id = drug_info.get("drug_id")
        if not drug_id:
            continue
        raw = build_drug_raw_summary(drug_id, db)
        if not raw:
            continue
        trials_obj = raw.get("trials") or {}
        trials_list = trials_obj.get("trials") or []
        if not isinstance(trials_list, list):
            continue
        for t in trials_list:
            if not isinstance(t, dict):
                continue
            conditions = t.get("conditions") or []
            if not isinstance(conditions, list):
                continue
            for c in conditions:
                norm = (c or "").strip().lower()
                if norm:
                    condition_counts[norm] = condition_counts.get(norm, 0) + 1

    sorted_conds = sorted(
        [{"condition": k, "count": v} for k, v in condition_counts.items()],
        key=lambda x: (-x["count"], x["condition"]),
    )
    return sorted_conds[:top_k]
