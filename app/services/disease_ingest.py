"""
Disease ingestion: genes, pathways, ClinVar, PubMed. Defensive (timeout <=10s, no crash).
"""
from __future__ import annotations

import re
import logging
from typing import Any

import requests

from app.config import get_settings

logger = logging.getLogger(__name__)

TIMEOUT = 10
BASE_ENTREZ = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Gene-like: 3-10 caps/numbers
GENE_SYMBOL_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]{2,9}\b")
# Junk to exclude from gene extraction
GENE_JUNK = frozenset({
    "THE", "AND", "FOR", "NOT", "ARE", "BUT", "HAS", "HAD", "WAS", "ALL", "CAN",
    "USA", "DNA", "RNA", "CDC", "NIH", "FDA", "WHO", "ICU", "MRI", "CT", "PCR",
    "ATP", "GTP", "NAD", "AMP", "GDP", "cAMP", "pH", "ATPase", "II", "III", "IV",
    "VIII", "IX", "XII", "IL", "IFN", "TNF", "MAPK", "JAK", "STAT", "NF", "PI3K",
    "ERK", "MEK", "mTOR", "UPR", "ROS", "NLRP", "cGAS", "STING",
})

PATHWAY_KEYWORDS = [
    "autophagy", "lysosome", "proteasome", "ubiquitin", "ER stress", "UPR",
    "mitochondria", "oxidative stress", "interferon", "type I interferon",
    "JAK-STAT", "NF-kB", "NFkB", "TNF", "IL-6", "NLRP3", "cGAS", "STING",
    "mTOR", "MAPK", "PI3K", "apoptosis", "cell cycle", "DNA repair",
    "glycolysis", "Wnt", "Hedgehog", "Notch", "calcium signaling",
    "kinase", "phosphatase", "receptor", "transcription factor",
]

CLINVAR_SIGNIFICANCE_BINS = [
    "Pathogenic", "Likely_pathogenic", "VUS", "Benign", "Likely_benign",
    "Conflicting", "Other",
]


def _get_ncbi_key() -> str:
    return (get_settings().ncbi_api_key or "").strip()


def _entrez_get(path: str, params: dict[str, str | int] | None = None) -> dict | None:
    url = f"{BASE_ENTREZ}/{path}"
    p = dict(params or {})
    if _get_ncbi_key():
        p["api_key"] = _get_ncbi_key()
    try:
        r = requests.get(url, params=p, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning("entrez_get_err path=%s err=%s", path, e)
        return None


def _detect_genes_from_query(query: str) -> list[str]:
    """If query looks like a gene symbol, return it. Else return []."""
    q = (query or "").strip().upper()
    if not q or len(q) > 10:
        return []
    if re.match(r"^[A-Z][A-Z0-9]{2,9}$", q) and q not in GENE_JUNK:
        return [q]
    return []


def _extract_genes_from_text(text: str, cap: int = 15) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in GENE_SYMBOL_PATTERN.finditer(text or ""):
        g = m.group(0).upper()
        if g in GENE_JUNK or g in seen:
            continue
        seen.add(g)
        out.append(g)
        if len(out) >= cap:
            break
    return out


def _pathway_counts(text: str) -> list[dict[str, Any]]:
    t = (text or "").lower()
    out = []
    for kw in PATHWAY_KEYWORDS:
        c = t.count(kw.lower())
        if c > 0:
            out.append({"term": kw, "count": c, "source": "keyword_dict"})
    return out


def _bucket_significance(s: str) -> str:
    s = (s or "").strip().lower()
    if "pathogenic" in s and "likely" not in s:
        return "Pathogenic"
    if "likely pathogenic" in s:
        return "Likely_pathogenic"
    if "uncertain" in s or "vus" in s or "variant of unknown" in s:
        return "VUS"
    if "benign" in s and "likely" not in s:
        return "Benign"
    if "likely benign" in s:
        return "Likely_benign"
    if "conflicting" in s:
        return "Conflicting"
    return "Other"


def _fetch_clinvar_for_genes(genes: list[str]) -> tuple[dict[str, int], list[dict[str, Any]], list[str]]:
    by_sig = {b: 0 for b in CLINVAR_SIGNIFICANCE_BINS}
    top_genes: list[dict[str, Any]] = []
    errors: list[str] = []
    for gene in genes[:10]:
        try:
            # ESearch ClinVar for gene
            term = f'{gene}[Gene Name]'
            data = _entrez_get("esearch.fcgi", {
                "db": "clinvar",
                "term": term,
                "retmax": 20,
                "retmode": "json",
            })
            if not data or "esearchresult" not in data:
                continue
            id_list = data.get("esearchresult", {}).get("idlist") or []
            if not id_list:
                continue
            # ESummary for variant summaries (simpler than efetch XML)
            ids = ",".join(id_list[:15])
            sum_data = _entrez_get("esummary.fcgi", {
                "db": "clinvar",
                "id": ids,
                "retmode": "json",
            })
            if not sum_data or "result" not in sum_data:
                continue
            gene_count = 0
            for uid, item in sum_data.get("result", {}).items():
                if uid == "uids":
                    continue
                if not isinstance(item, dict):
                    continue
                clnsig = (item.get("clinical_significance") or item.get("clinical_significance_description") or "").strip()
                if clnsig:
                    bucket = _bucket_significance(clnsig)
                    by_sig[bucket] = by_sig.get(bucket, 0) + 1
                    gene_count += 1
            if gene_count > 0:
                top_genes.append({"gene": gene, "variant_count": gene_count})
        except Exception as e:
            errors.append(f"ClinVar {gene}: {e}")
        # Rate limit: avoid hammering
        import time
        time.sleep(0.4)
    top_genes.sort(key=lambda x: -x["variant_count"])
    return by_sig, top_genes[:10], errors


def _fetch_pubmed(canonical_name: str, genes: list[str]) -> tuple[int, dict[str, int], list[dict], list[str]]:
    total = 0
    by_year: dict[str, int] = {}
    recent: list[dict] = []
    errors: list[str] = []
    try:
        terms = [canonical_name] + genes[:5]
        query = " OR ".join(f'"{t}"' for t in terms)
        data = _entrez_get("esearch.fcgi", {
            "db": "pubmed",
            "term": query,
            "retmax": 0,
            "retmode": "json",
        })
        if data and "esearchresult" in data:
            total = int(data.get("esearchresult", {}).get("count", 0) or 0)

        from datetime import datetime
        current_year = datetime.utcnow().year
        for y in range(current_year, current_year - 5, -1):
            yr_str = str(y)
            dp = f"{yr_str}/01/01:{yr_str}/12/31"
            d = _entrez_get("esearch.fcgi", {
                "db": "pubmed",
                "term": query,
                "datetype": "pdat",
                "mindate": f"{yr_str}/01/01",
                "maxdate": f"{yr_str}/12/31",
                "retmax": 0,
                "retmode": "json",
            })
            if d and "esearchresult" in d:
                by_year[yr_str] = int(d.get("esearchresult", {}).get("count", 0) or 0)

        # Recent 10
        d2 = _entrez_get("esearch.fcgi", {
            "db": "pubmed",
            "term": query,
            "retmax": 10,
            "retmode": "json",
            "sort": "date",
        })
        if d2 and "esearchresult" in d2:
            id_list = d2.get("esearchresult", {}).get("idlist") or []
            if id_list:
                ids = ",".join(id_list)
                sum_data = _entrez_get("esummary.fcgi", {
                    "db": "pubmed",
                    "id": ids,
                    "retmode": "json",
                })
                if sum_data and "result" in sum_data:
                    for uid in id_list:
                        item = sum_data.get("result", {}).get(uid)
                        if not isinstance(item, dict):
                            continue
                        title = item.get("title") or ""
                        pubdate = item.get("pubdate") or ""
                        yr = None
                        if pubdate:
                            try:
                                yr = int(pubdate[:4])
                            except (ValueError, TypeError):
                                pass
                        recent.append({
                            "pmid": uid,
                            "title": title,
                            "year": yr,
                            "url": f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                        })
                        if len(recent) >= 10:
                            break
    except Exception as e:
        errors.append(f"PubMed: {e}")
    return total, by_year, recent[:10], errors


def ingest_disease(disease: Any, query_hint: str | None = None) -> dict:
    """
    Ingest disease: genes, phenotype/pathway terms, ClinVar, PubMed.
    Returns raw_summary dict. Never raises; failures go to errors and source_status.
    """
    disease_id = getattr(disease, "id", "") or ""
    canonical_name = getattr(disease, "canonical_name", "") or ""
    ids_json = getattr(disease, "ids_json", None) or {}
    orpha = ids_json.get("orpha")
    omim = ids_json.get("omim")

    source_status = {
        "orphanet": "skipped",
        "clinvar": "skipped",
        "pubmed": "skipped",
        "omim": "skipped",
    }
    errors: list[str] = []
    synonyms: list[str] = []
    overview_text = ""

    # Orphanet: best-effort only; skip if not feasible (no public API key / endpoint)
    source_status["orphanet"] = "skipped"
    if orpha:
        try:
            # Orphanet XML API (public, no key) - single disease by ORPHA code
            url = f"https://www.orpha.net/ors/GetContent?lng=EN&id={orpha}"
            r = requests.get(url, timeout=TIMEOUT)
            if r.status_code == 200 and r.text:
                # May be HTML or XML; parse loosely for overview text
                text = r.text[:5000]
                if "Orpha" in text or "disease" in text.lower():
                    overview_text = text[:2000]
                    source_status["orphanet"] = "ok"
        except Exception as e:
            errors.append(f"Orphanet: {e}")
            source_status["orphanet"] = "error"

    # Genes
    genes_from_query = _detect_genes_from_query(query_hint or canonical_name)
    text_for_genes = " ".join([canonical_name, overview_text] + synonyms)
    genes_from_text = _extract_genes_from_text(text_for_genes, cap=15)
    all_genes: list[dict] = []
    seen_g = set()
    for g in genes_from_query:
        if g not in seen_g:
            seen_g.add(g)
            all_genes.append({"symbol": g, "source": "heuristic"})
    for g in genes_from_text:
        if g not in seen_g:
            seen_g.add(g)
            all_genes.append({"symbol": g, "source": "text"})
    gene_symbols = [x["symbol"] for x in all_genes[:15]]

    # Phenotype terms: from overview/synonyms as simple word counts (simplified)
    phenotype_terms: list[dict] = []
    for term in (overview_text or "").split():
        t = term.strip(".,;:").lower()
        if len(t) >= 4 and t not in ("that", "with", "this", "from", "have", "were", "their"):
            phenotype_terms.append({"term": t, "count": 1, "source": "text"})
    # Aggregate
    from collections import Counter
    pheno_agg: Counter[str] = Counter()
    for p in phenotype_terms:
        pheno_agg[p["term"]] += p["count"]
    phenotype_terms = [{"term": k, "count": v, "source": "text"} for k, v in pheno_agg.most_common(30)]

    # Pathway terms
    pathway_terms = _pathway_counts(" ".join([overview_text, " ".join(synonyms)]))

    # ClinVar
    clinvar_by_sig: dict[str, int] = {b: 0 for b in CLINVAR_SIGNIFICANCE_BINS}
    clinvar_top_genes: list[dict] = []
    if gene_symbols:
        try:
            clinvar_by_sig, clinvar_top_genes, cv_errs = _fetch_clinvar_for_genes(gene_symbols)
            errors.extend(cv_errs)
            source_status["clinvar"] = "ok" if clinvar_top_genes or any(clinvar_by_sig.values()) else "skipped"
        except Exception as e:
            errors.append(f"ClinVar: {e}")
            source_status["clinvar"] = "error"

    # PubMed
    pubs_total = 0
    pubs_by_year: dict[str, int] = {}
    pubs_recent: list[dict] = []
    try:
        pubs_total, pubs_by_year, pubs_recent, pub_errs = _fetch_pubmed(canonical_name, gene_symbols)
        errors.extend(pub_errs)
        source_status["pubmed"] = "ok" if pubs_total > 0 or pubs_recent else "skipped"
    except Exception as e:
        errors.append(f"PubMed: {e}")
        source_status["pubmed"] = "error"

    raw = {
        "disease_id": disease_id,
        "canonical_name": canonical_name,
        "ids": {"orpha": orpha, "omim": omim},
        "synonyms": synonyms[:25],
        "genes": all_genes[:15],
        "phenotype_terms": phenotype_terms,
        "pathway_terms": pathway_terms,
        "clinvar": {
            "by_significance": clinvar_by_sig,
            "top_genes": clinvar_top_genes[:10],
        },
        "publications": {
            "total": pubs_total,
            "by_year": pubs_by_year,
            "recent": pubs_recent[:10],
        },
        "source_status": source_status,
        "errors": errors[:20],
    }
    return raw
