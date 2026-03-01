"""
Deterministic mechanism-space vectorizer (Block 2).

Converts drug_short_v1 / disease_short_v1 dicts into:
  - sparse: Dict[node -> {weight, direction, evidence}]
  - dense_weights: list[float] aligned to MECH_NODES order
  - dense_direction: list[int]  aligned to MECH_NODES order

No LLM calls. Pure functions — same input → same output.
"""
from __future__ import annotations

import math
import re
from typing import Any

from app.services.disease_direction import infer_disease_node_directions
from app.services.mechanism_vocab import (
    GENE_TO_NODES,
    MECH_ALIASES,
    MECH_NODES,
)

# ---------------------------------------------------------------------------
# Module-level: pre-normalise aliases once for performance + determinism
# ---------------------------------------------------------------------------
_INHIBITORY_TOKENS: frozenset[str] = frozenset(
    {"inhibitor", "antagonist", "blocker", "inverse agonist", "suppressor",
     "inhibits", "blocks", "negative allosteric", "negative modulator"}
)
_ACTIVATING_TOKENS: frozenset[str] = frozenset(
    {"agonist", "activator", "stimulator", "potentiator", "enhancer", "upregulator"}
)

_WEIGHT_THRESHOLD = 0.08   # nodes below this are excluded from sparse output
_MAX_EVIDENCE_PER_NODE = 4
_TARGET_BASE_SCORE = 0.5   # raw score added per target-alias match
_GENE_BASE_SCORE = 1.0     # raw score added per gene-to-node match


def _norm(s: str) -> str:
    """Replace non-alphanumeric with space, collapse, lowercase."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


# Normalised aliases indexed by node — computed once at import time.
_ALIASES_NORM: dict[str, list[str]] = {
    node: [a for a in (_norm(alias) for alias in aliases) if a]
    for node, aliases in MECH_ALIASES.items()
}


# ---------------------------------------------------------------------------
# Public helpers (defensive: support multiple schema shapes)
# ---------------------------------------------------------------------------

def normalize_text(s: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not s:
        return ""
    return _norm(s)


# Keys to try for pathway extraction (order matters; first found wins)
PATHWAY_KEYS = (
    "pathways_top",
    "pathway_terms_top",
    "pathway_mentions_top",
    "pathways",
    "mechanism_terms",
)


def extract_pathway_terms(obj: dict) -> list[tuple[str, float]]:
    """
    Defensively extract (term, value) pairs from a summary object.
    Tries PATHWAY_KEYS; supports nested structures like {"pathways_top": {"items": [...]}}.
    Never raises; returns [] on missing or invalid data.
    """
    if not obj or not isinstance(obj, dict):
        return []
    raw: Any = None
    for key in PATHWAY_KEYS:
        v = obj.get(key)
        if v is None:
            continue
        if isinstance(v, list):
            raw = v
            break
        if isinstance(v, dict):
            # Nested: e.g. {"top": [...]} or {"items": [...]}
            raw = v.get("top") or v.get("items") or v.get("pathways") or list(v.values())[:1]
            if isinstance(raw, list):
                break
            raw = None
    if raw is None or not isinstance(raw, list):
        return []
    return extract_term_list_from_pathways(raw)


def extract_term_list_from_pathways(obj: Any) -> list[tuple[str, float]]:
    """
    Robustly extract (term, value) pairs from:
      - [{"term": ..., "count": ...}]
      - [{"term": ..., "score": ...}]
      - [{"term": ..., "weight": ...}]
      - ["string", ...]
    Returns sorted list of (term, float) — sorted by -value, term for determinism.
    """
    if not obj:
        return []
    result: list[tuple[str, float]] = []
    for item in obj:
        if isinstance(item, dict):
            term = str(item.get("term") or item.get("name") or "").strip()
            raw_val = item.get("count") or item.get("score") or item.get("weight") or 1.0
            try:
                value = float(raw_val)
            except (TypeError, ValueError):
                value = 1.0
        elif isinstance(item, str):
            term = item.strip()
            value = 1.0
        else:
            continue
        if term:
            result.append((term, value))
    # Sort deterministically: highest value first, then alphabetically
    result.sort(key=lambda x: (-x[1], x[0]))
    return result


def extract_targets(obj: dict) -> list[tuple[str, str, str]]:
    """
    Defensively extract (name, gene, action) triples from a summary object.
    Supports: list[str], list[{"name":...}], list[{"symbol":...}], list[{"target":...}],
    list[{"target_name":..., "gene_symbol":..., "action":...}].
    Keys tried: targets_top, targets, target_list.
    Never raises; returns [] on missing or invalid data.
    """
    if not obj or not isinstance(obj, dict):
        return []
    raw = obj.get("targets_top") or obj.get("targets") or obj.get("target_list")
    if not isinstance(raw, list):
        return []
    result: list[tuple[str, str, str]] = []
    for t in raw:
        if isinstance(t, str):
            result.append((t.strip(), "", ""))
        elif isinstance(t, dict):
            name = str(
                t.get("name") or t.get("target_name") or t.get("target") or ""
            ).strip()
            gene = str(
                t.get("gene") or t.get("gene_symbol") or t.get("symbol") or ""
            ).strip().upper()
            action = str(t.get("action") or "").strip()
            if name or gene:
                result.append((name, gene, action))
    result.sort(key=lambda x: x[0])
    return result


# ---------------------------------------------------------------------------
# Core matching
# ---------------------------------------------------------------------------

def _match_nodes_from_terms(
    terms: list[tuple[str, float]],
) -> tuple[dict[str, float], dict[str, list[str]]]:
    """
    Scan pathway/phenotype terms against MECH_ALIASES.
    Returns (raw_scores, evidence) dicts.

    Matching rule:
      term_norm contains alias  OR  alias contains term_norm (≥3 chars)
    For each term, at most one alias match per node (first alias wins).
    """
    raw: dict[str, float] = {}
    evid: dict[str, list[str]] = {}

    for term, value in terms:
        term_norm = _norm(term)
        if not term_norm:
            continue
        for node, aliases in _ALIASES_NORM.items():
            for alias in aliases:
                if not alias:
                    continue
                if alias in term_norm or (len(term_norm) >= 3 and term_norm in alias):
                    raw[node] = raw.get(node, 0.0) + value
                    ev_str = f"pathway:{term}({value:.1f})"
                    ev_list = evid.setdefault(node, [])
                    if ev_str not in ev_list:
                        ev_list.append(ev_str)
                    break  # first alias match per node per term

    return raw, evid


def _infer_drug_directions(
    drug_short: dict,
    targets: list[tuple[str, str, str]],
    active_nodes: set[str],
) -> dict[str, int]:
    """
    Best-effort direction per node from target actions, pathways, canonical_name.
    Inhibitory keywords → -1; activating → +1; ambiguous → 0.
    First deterministic match wins per node.
    """
    dirs: dict[str, int] = {}
    drug_short = drug_short or {}

    def _text_direction(text: str) -> int:
        t = _norm(text)
        if not t:
            return 0
        for tok in _INHIBITORY_TOKENS:
            if tok in t:
                return -1
        for tok in _ACTIVATING_TOKENS:
            if tok in t:
                return 1
        return 0

    # 1. Target actions (highest confidence)
    for name, gene, action in targets:
        d = _text_direction(action)
        if d == 0:
            d = _text_direction(name)
        if d == 0:
            continue
        if gene and gene in GENE_TO_NODES:
            for node in GENE_TO_NODES[gene]:
                if node in active_nodes and node not in dirs:
                    dirs[node] = d
        name_norm = _norm(name)
        for node in active_nodes:
            if node in dirs:
                continue
            for alias in _ALIASES_NORM.get(node, []):
                if alias and (alias in name_norm or (len(name_norm) >= 3 and name_norm in alias)):
                    dirs[node] = d
                    break

    # 2. Pathways text (if no direction from targets)
    pathways = extract_pathway_terms(drug_short)
    for term, _ in pathways:
        d = _text_direction(term)
        if d == 0:
            continue
        term_norm = _norm(term)
        for node in active_nodes:
            if node in dirs:
                continue
            for alias in _ALIASES_NORM.get(node, []):
                if alias and (alias in term_norm or (len(term_norm) >= 3 and term_norm in alias)):
                    dirs[node] = d
                    break

    # 3. Canonical name
    canon = _text_direction(drug_short.get("canonical_name") or "")
    if canon != 0:
        for node in active_nodes:
            if node not in dirs:
                dirs[node] = canon
                break  # apply to first unmatched only

    return dirs


def _finalize_sparse(
    raw: dict[str, float],
    evid: dict[str, list[str]],
    directions: dict[str, int] | None,
) -> dict:
    """
    Normalise raw scores, apply threshold, cap evidence, sort output.
    Returns sparse dict ordered by (-weight, node_name).
    """
    if not raw:
        return {}
    max_raw = max(raw.values())
    if max_raw <= 0.0:
        return {}

    result: dict[str, dict] = {}
    for node, score in raw.items():
        weight = round(score / max_raw, 4)
        if weight < _WEIGHT_THRESHOLD:
            continue
        ev_sorted = sorted(set(evid.get(node, [])))[:_MAX_EVIDENCE_PER_NODE]
        result[node] = {
            "weight": weight,
            "direction": (directions or {}).get(node, 0),
            "evidence": ev_sorted,
        }

    # Stable sort: highest weight first, then node name ascending
    return dict(sorted(result.items(), key=lambda kv: (-kv[1]["weight"], kv[0])))


# ---------------------------------------------------------------------------
# Public vectorization functions
# ---------------------------------------------------------------------------

def drug_to_mech_vector(drug_short: dict) -> dict:
    """
    Convert drug_short_v1 dict to sparse mechanism vector.

    Sources:
      1. pathways_top → alias matching
      2. targets_top  → gene boost + target alias boost + direction inference
    """
    raw: dict[str, float] = {}
    evid: dict[str, list[str]] = {}

    # 1. Pathway matching (defensive: try pathways_top and other keys)
    pathways = extract_pathway_terms(drug_short)
    r_pw, e_pw = _match_nodes_from_terms(pathways)
    for node, val in r_pw.items():
        raw[node] = raw.get(node, 0.0) + val
        evid.setdefault(node, []).extend(e_pw.get(node, []))

    # 2. Target hints
    targets = extract_targets(drug_short)
    for name, gene, _action in targets:
        name_norm = _norm(name)

        # Gene-symbol lookup in GENE_TO_NODES
        if gene and gene in GENE_TO_NODES:
            for node in GENE_TO_NODES[gene]:
                raw[node] = raw.get(node, 0.0) + _TARGET_BASE_SCORE
                ev_str = f"target:{gene}"
                ev_list = evid.setdefault(node, [])
                if ev_str not in ev_list:
                    ev_list.append(ev_str)

        # Alias match on target name
        for node, aliases in _ALIASES_NORM.items():
            for alias in aliases:
                if alias and (alias in name_norm or (len(name_norm) >= 3 and name_norm in alias)):
                    raw[node] = raw.get(node, 0.0) + _TARGET_BASE_SCORE
                    ev_str = f"target:{name or gene}"
                    ev_list = evid.setdefault(node, [])
                    if ev_str not in ev_list:
                        ev_list.append(ev_str)
                    break  # first alias per node per target name

    # 3. Direction inference
    directions = _infer_drug_directions(drug_short, targets, set(raw.keys()))

    return _finalize_sparse(raw, evid, directions)


def disease_to_mech_vector(disease_short: dict) -> dict:
    """
    Convert disease_short_v1 dict to sparse mechanism vector.

    Sources:
      1. pathways_top  → alias matching (full weight)
      2. phenotypes_top → alias matching (0.5× weight)
      3. genes          → GENE_TO_NODES boost (+1.0 each, cap 10 genes)

    Direction always 0 in v0.
    """
    raw: dict[str, float] = {}
    evid: dict[str, list[str]] = {}

    # 1. Pathway terms (defensive: try pathways_top and other keys)
    pathways = extract_pathway_terms(disease_short)
    r_pw, e_pw = _match_nodes_from_terms(pathways)
    for node, val in r_pw.items():
        raw[node] = raw.get(node, 0.0) + val
        evid.setdefault(node, []).extend(e_pw.get(node, []))

    # 2. Phenotype terms (lower weight — phenotypes are more indirect)
    phenotypes_raw = disease_short.get("phenotypes_top") or disease_short.get("phenotype_terms") or []
    phenotypes = extract_term_list_from_pathways(
        phenotypes_raw if isinstance(phenotypes_raw, list) else []
    )
    r_ph, e_ph = _match_nodes_from_terms(phenotypes)
    for node, val in r_ph.items():
        raw[node] = raw.get(node, 0.0) + val * 0.5
        ph_evid = [f"phenotype:{e}" for e in e_ph.get(node, [])]
        ev_list = evid.setdefault(node, [])
        for ev_str in ph_evid:
            if ev_str not in ev_list:
                ev_list.append(ev_str)

    # 3. Gene boosts (cap at 10)
    genes_raw = disease_short.get("genes") or []
    genes = [
        (g if isinstance(g, str) else str(g)).strip().upper()
        for g in genes_raw
        if (g if isinstance(g, str) else str(g)).strip()
    ][:10]

    for gene in genes:
        if gene not in GENE_TO_NODES:
            continue
        for node in GENE_TO_NODES[gene]:
            raw[node] = raw.get(node, 0.0) + _GENE_BASE_SCORE
            ev_str = f"gene:{gene}"
            ev_list = evid.setdefault(node, [])
            if ev_str not in ev_list:
                ev_list.append(ev_str)

    # Direction from phenotype/ClinVar heuristics
    disease_dirs = infer_disease_node_directions(disease_short, raw)
    return _finalize_sparse(raw, evid, disease_dirs)


# ---------------------------------------------------------------------------
# Dense conversion
# ---------------------------------------------------------------------------

def sparse_to_dense_weights(sparse: dict) -> list[float]:
    """Return weight vector aligned to MECH_NODES. Missing nodes → 0.0."""
    return [
        float(sparse[node]["weight"]) if node in sparse else 0.0
        for node in MECH_NODES
    ]


def sparse_to_dense_direction(sparse: dict) -> list[int]:
    """Return direction vector aligned to MECH_NODES. Missing nodes → 0."""
    return [
        int(sparse[node]["direction"]) if node in sparse else 0
        for node in MECH_NODES
    ]


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Cosine similarity between two dense vectors.
    Returns 0.0 for zero vectors or length mismatch.
    Result rounded to 6 decimal places.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return round(dot / (mag_a * mag_b), 6)
