"""
Deterministic compactor: raw disease summary -> short summary for UI. No LLM.
"""
from __future__ import annotations

PHENOTYPE_EXCLUDE = frozenset({
    "disease", "syndrome", "patients", "clinical", "mutation", "gene", "rare",
})
PATHWAY_EXCLUDE = frozenset({
    "pathway", "signaling", "activation", "immune",
})
CLINVAR_BINS = [
    "Pathogenic", "Likely_pathogenic", "VUS", "Benign", "Likely_benign",
    "Conflicting", "Other",
]


def compact_disease_summary(raw_summary: dict) -> dict:
    """Same input -> same output. Output schema exactly as specified."""
    disease_id = raw_summary.get("disease_id") or ""
    canonical_name = raw_summary.get("canonical_name") or ""
    ids_raw = raw_summary.get("ids") or {}
    ids: dict[str, str] = {}
    if ids_raw.get("omim"):
        ids["omim"] = str(ids_raw["omim"])
    if ids_raw.get("orpha"):
        ids["orpha"] = str(ids_raw["orpha"])

    genes_list = raw_summary.get("genes") or []
    seen = set()
    genes: list[str] = []
    for g in genes_list:
        if isinstance(g, dict):
            s = (g.get("symbol") or "").strip()
        else:
            s = str(g).strip()
        if s and s not in seen:
            seen.add(s)
            genes.append(s)
        if len(genes) >= 10:
            break
    genes = genes[:10]

    phenotype_terms_raw = raw_summary.get("phenotype_terms") or []
    pheno_agg: dict[str, int] = {}
    for p in phenotype_terms_raw:
        if not isinstance(p, dict):
            continue
        term = (p.get("term") or "").strip().lower()
        if term in PHENOTYPE_EXCLUDE:
            continue
        pheno_agg[term] = pheno_agg.get(term, 0) + (p.get("count") or 0)
    phenotypes_top = sorted(
        [{"term": k, "count": v} for k, v in pheno_agg.items()],
        key=lambda x: (-x["count"], x["term"]),
    )[:12]

    pathway_terms_raw = raw_summary.get("pathway_terms") or []
    path_agg: dict[str, int] = {}
    for p in pathway_terms_raw:
        if not isinstance(p, dict):
            continue
        term = (p.get("term") or "").strip().lower()
        if term in PATHWAY_EXCLUDE:
            continue
        path_agg[term] = path_agg.get(term, 0) + (p.get("count") or 0)
    pathways_top = sorted(
        [{"term": k, "count": v} for k, v in path_agg.items()],
        key=lambda x: (-x["count"], x["term"]),
    )[:12]

    clinvar_raw = raw_summary.get("clinvar") or {}
    by_sig_raw = clinvar_raw.get("by_significance") or {}
    by_significance = {b: int(by_sig_raw.get(b, 0) or 0) for b in CLINVAR_BINS}
    top_genes_raw = clinvar_raw.get("top_genes") or []
    top_genes = []
    for t in top_genes_raw[:8]:
        if isinstance(t, dict):
            top_genes.append({
                "gene": str(t.get("gene") or "").strip(),
                "variant_count": int(t.get("variant_count") or 0),
            })
    clinvar_top = {
        "by_significance": by_significance,
        "top_genes": top_genes,
    }

    pubs = raw_summary.get("publications") or {}
    total = int(pubs.get("total") or 0)
    by_year_raw = pubs.get("by_year") or {}
    from collections import OrderedDict
    years_sorted = sorted(by_year_raw.keys(), reverse=True)[:4]
    pubs_recent_years = OrderedDict((y, int(by_year_raw.get(y, 0) or 0)) for y in years_sorted)
    stats = {
        "pubs_total": total,
        "pubs_recent_years": dict(pubs_recent_years),
    }

    source_status = raw_summary.get("source_status") or {}
    notes: list[str] = []
    if source_status.get("orphanet") != "ok":
        notes.append("No Orphanet data")
    if not genes:
        notes.append("No genes detected")
    if source_status.get("clinvar") != "ok":
        notes.append("ClinVar unavailable")
    notes = notes[:6]

    return {
        "disease_id": disease_id,
        "canonical_name": canonical_name,
        "ids": ids,
        "genes": genes,
        "phenotypes_top": phenotypes_top,
        "pathways_top": pathways_top,
        "clinvar_top": clinvar_top,
        "stats": stats,
        "notes": notes,
        "version": "disease_short_v1",
    }
