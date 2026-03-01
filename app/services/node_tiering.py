"""
Block 5 — Node evidence tiering.

Deterministic. Assigns support tiers to mechanism nodes based on evidence type.
"""
from __future__ import annotations

from app.services.mechanism_vocab import GENE_TO_NODES, MECH_ALIASES


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


def _node_in_pathways(pathways_top: list, node: str) -> bool:
    """True if node appears in pathways_top terms."""
    if not isinstance(pathways_top, list):
        return False
    for p in pathways_top:
        if not isinstance(p, dict):
            continue
        term = (p.get("term") or "").strip()
        if term and _term_matches_node(term, node):
            return True
    return False


def _node_supported_by_target(targets_top: list, node: str) -> bool:
    """True if any target gene maps to node via GENE_TO_NODES."""
    if not isinstance(targets_top, list):
        return False
    for t in targets_top:
        if not isinstance(t, dict):
            continue
        gene = (t.get("gene") or "").strip().upper()
        if gene and gene in GENE_TO_NODES and node in GENE_TO_NODES[gene]:
            return True
    return False


def _has_phase3(phase_counts: dict) -> bool:
    """True if any phase key contains PHASE3."""
    if not isinstance(phase_counts, dict):
        return False
    for k in phase_counts:
        if k and "PHASE3" in str(k).upper():
            return True
    return False


def compute_node_tiers(drug_short: dict, node_weights: dict) -> dict:
    """
    Compute evidence tier per node. Deterministic.

    Tier 0: pathway_text only
    Tier 1: target evidence (gene maps to node)
    Tier 2: ≥1 trial (Phase 1+)
    Tier 3: ≥3 trials OR ≥1 Phase 3 trial

    Returns:
      { node_name: { "tier": int, "support": [str], "weight": float } }
    """
    drug_short = drug_short or {}
    node_weights = node_weights or {}

    pathways_top = drug_short.get("pathways_top") or []
    targets_top = drug_short.get("targets_top") or drug_short.get("targets") or []
    trials = drug_short.get("trials") or {}
    total_trials = int(trials.get("total", 0)) if isinstance(trials, dict) else 0
    phase_counts = trials.get("phase_counts") or trials.get("by_phase") or {}
    has_phase3 = _has_phase3(phase_counts)

    result: dict = {}
    for node, weight_val in node_weights.items():
        if not node:
            continue
        w = float(weight_val.get("weight", 0.0)) if isinstance(weight_val, dict) else float(weight_val or 0)
        if w <= 0:
            continue

        support: list[str] = []
        tier = 0

        if _node_in_pathways(pathways_top, node):
            support.append("pathway_text")

        if _node_supported_by_target(targets_top, node):
            support.append("target")
            if tier < 1:
                tier = 1

        if total_trials > 0:
            support.append("trial_exposure")
            if tier < 2:
                tier = 2

        if total_trials >= 3 or has_phase3:
            if tier < 3:
                tier = 3

        result[node] = {
            "tier": tier,
            "support": support,
            "weight": round(w, 4),
        }

    return result
