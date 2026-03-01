"""
Block 6 — Disease polarity heuristics.

Deterministic. Infer node direction from phenotype terms and ClinVar.
"""
from __future__ import annotations

_POSITIVE_KEYWORDS = frozenset({
    "hyperactivation", "overactivation", "elevated", "excess",
    "inflammatory", "overproduction",
})
_NEGATIVE_KEYWORDS = frozenset({
    "deficiency", "loss", "impaired", "reduced", "insufficient", "hypo",
})


def infer_disease_node_directions(disease_short: dict, sparse_nodes: dict) -> dict[str, int]:
    """
    Infer direction per node from phenotype terms and ClinVar.
    Conservative: default 0. Only set ±1 when textual hint exists.
    Returns { node_name: direction } where direction ∈ {-1, 0, +1}.
    """
    disease_short = disease_short or {}
    sparse_nodes = sparse_nodes or {}
    result: dict[str, int] = {node: 0 for node in sparse_nodes}

    # Collect all phenotype/pathway text for keyword matching
    text_parts: list[str] = []
    phenotypes = disease_short.get("phenotypes_top") or disease_short.get("phenotype_terms") or []
    for p in phenotypes if isinstance(phenotypes, list) else []:
        if isinstance(p, dict):
            t = (p.get("term") or "").strip().lower()
            if t:
                text_parts.append(t)
        elif isinstance(p, str):
            text_parts.append(p.lower())
    pathways = disease_short.get("pathways_top") or disease_short.get("pathway_terms") or []
    for p in pathways if isinstance(pathways, list) else []:
        if isinstance(p, dict):
            t = (p.get("term") or "").strip().lower()
            if t:
                text_parts.append(t)
    combined_text = " ".join(text_parts)

    # ClinVar: gain-of-function hint → +1 for affected nodes
    clinvar = disease_short.get("clinvar_top") or disease_short.get("clinvar") or {}
    by_sig = clinvar.get("by_significance") or clinvar.get("by_significance") or {}
    if isinstance(by_sig, dict):
        gof_count = 0
        for k, v in by_sig.items():
            k_lower = (k or "").lower()
            if "gain" in k_lower or "function" in k_lower or "gof" in k_lower:
                gof_count += int(v) if isinstance(v, (int, float)) else 0
        if gof_count > 0:
            for node in sparse_nodes:
                result[node] = 1

    # Phenotype keywords override
    has_positive = any(kw in combined_text for kw in _POSITIVE_KEYWORDS)
    has_negative = any(kw in combined_text for kw in _NEGATIVE_KEYWORDS)

    if has_positive and not has_negative:
        for node in sparse_nodes:
            result[node] = 1
    elif has_negative and not has_positive:
        for node in sparse_nodes:
            result[node] = -1
    elif has_positive and has_negative:
        pass  # conflicting — keep 0

    return result
