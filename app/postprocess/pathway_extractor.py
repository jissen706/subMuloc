"""
Pathway keyword extractor (post-processing, DB read-only input).

Reads from:
  publication (abstracts), trial (title + conditions), target (target_name)

Writes to:
  disease_pathway_mention, evidence(pathway)

No scoring or ranking — only keyword matching with a heuristic confidence:
  - title match:    confidence 0.9
  - abstract match: confidence 0.7
  - target match:   confidence 0.8
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import DiseasePathwayMention, Evidence, Publication, Target, Trial
from app.utils.text import extract_snippet, find_keywords

logger = logging.getLogger(__name__)

PATHWAY_KEYWORDS: list[str] = [
    # Autophagy / mitophagy
    "autophagy", "mitophagy", "autophagic", "autophagosome", "beclin",
    # Innate immunity
    "STING", "cGAS", "cGAS-STING", "inflammasome", "NLRP3",
    # Signaling cascades
    "NF-kB", "NFkB", "NF-κB",
    "MAPK", "ERK", "MEK",
    "JAK-STAT", "JAK/STAT", "STAT3", "STAT1",
    "mTOR", "mTORC1", "mTORC2", "PI3K", "AKT",
    "Wnt", "Hedgehog", "Notch",
    "VEGF", "angiogenesis",
    "HER2", "EGFR", "KRAS", "BRAF", "RAS",
    # Cell death
    "apoptosis", "apoptotic", "caspase", "necroptosis", "ferroptosis",
    "senescence", "pyroptosis",
    # Immune / inflammation
    "retroviral activation", "endogenous retrovirus", "ERV",
    "interferon signaling", "interferon", "IFN", "innate immune",
    "T cell", "checkpoint", "PD-1", "PD-L1", "CTLA-4",
    "cytokine", "IL-6", "TNF", "tumor necrosis factor",
    # Mitochondria / metabolism
    "lysosomal dysfunction", "lysosome",
    "mitochondrial dysfunction", "mitochondria", "oxidative phosphorylation",
    "reactive oxygen species", "ROS",
    "glycolysis", "Warburg", "metabolism",
    # DNA damage
    "DNA damage", "PARP", "ATM", "ATR", "homologous recombination",
    "mismatch repair", "MMR", "microsatellite instability", "MSI",
    "BRCA",
    # Epigenetics
    "epigenetic", "histone deacetylase", "HDAC", "methylation", "acetylation",
]


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _store_mention(
    session: Session,
    drug_id: str,
    pathway_term: str,
    evidence_source: str,
    evidence_ref: str | None,
    snippet: str,
    confidence: float,
    disease_name: str | None = None,
) -> None:
    session.add(DiseasePathwayMention(
        id=_new_uuid(),
        drug_id=drug_id,
        disease_name=disease_name,
        pathway_term=pathway_term,
        evidence_source=evidence_source,
        evidence_ref=evidence_ref,
        snippet=snippet[:1000] if snippet else None,
        confidence=round(confidence, 2),
    ))


def _store_evidence(
    session: Session,
    drug_id: str,
    pathway_term: str,
    evidence_source: str,
    snippet: str,
    url: str | None = None,
    meta: dict | None = None,
) -> None:
    session.add(Evidence(
        id=_new_uuid(),
        drug_id=drug_id,
        source=evidence_source,
        evidence_type="pathway",
        title=f"Pathway mention: {pathway_term}",
        snippet_text=snippet[:512] if snippet else None,
        url=url,
        metadata_json=meta,
    ))


def run(session: Session, drug_id: str) -> int:
    """
    Extract pathway keyword mentions for the given drug.
    Returns the number of disease_pathway_mention rows created.
    """
    session.execute(delete(DiseasePathwayMention).where(DiseasePathwayMention.drug_id == drug_id))
    count = 0

    # -----------------------------------------------------------------------
    # 1. Publications (title + abstract)
    # -----------------------------------------------------------------------
    pubs = session.execute(
        select(Publication).where(Publication.drug_id == drug_id)
    ).scalars().all()

    for pub in pubs:
        title = pub.title or ""
        abstract = pub.abstract or ""

        title_hits = find_keywords(title, PATHWAY_KEYWORDS)
        abstract_hits = find_keywords(abstract, PATHWAY_KEYWORDS)

        # Collect all hits (with best confidence)
        best: dict[str, float] = {}
        for kw in title_hits:
            best[kw] = max(best.get(kw, 0), 0.9)
        for kw in abstract_hits:
            best[kw] = max(best.get(kw, 0), 0.7)

        for kw, conf in best.items():
            text_to_search = title + " " + abstract
            snippet = extract_snippet(text_to_search, kw, window=200)
            _store_mention(
                session, drug_id,
                pathway_term=kw,
                evidence_source="pubmed",
                evidence_ref=pub.pmid,
                snippet=snippet,
                confidence=conf,
            )
            _store_evidence(
                session, drug_id,
                pathway_term=kw,
                evidence_source="pubmed",
                snippet=snippet,
                url=pub.url,
                meta={"pmid": pub.pmid, "year": pub.year},
            )
            count += 1

    # -----------------------------------------------------------------------
    # 2. Trials (title + conditions)
    # -----------------------------------------------------------------------
    trials = session.execute(
        select(Trial).where(Trial.drug_id == drug_id)
    ).scalars().all()

    for trial in trials:
        combined = " ".join(filter(None, [
            trial.title,
            " ".join(trial.conditions_json) if isinstance(trial.conditions_json, list) else "",
        ]))
        hits = find_keywords(combined, PATHWAY_KEYWORDS)
        for kw in hits:
            snippet = extract_snippet(combined, kw, window=150)
            _store_mention(
                session, drug_id,
                pathway_term=kw,
                evidence_source="ctgov",
                evidence_ref=trial.nct_id,
                snippet=snippet,
                confidence=0.6,
            )
            count += 1

    # -----------------------------------------------------------------------
    # 3. Targets (target_name → pathway term)
    # -----------------------------------------------------------------------
    targets = session.execute(
        select(Target).where(Target.drug_id == drug_id)
    ).scalars().all()

    for target in targets:
        text = " ".join(filter(None, [
            target.target_name,
            target.gene_symbol,
            str(target.evidence) if target.evidence else "",
        ]))
        hits = find_keywords(text, PATHWAY_KEYWORDS)
        for kw in hits:
            snippet = extract_snippet(text, kw, window=100)
            _store_mention(
                session, drug_id,
                pathway_term=kw,
                evidence_source="chembl",
                evidence_ref=str(target.id),
                snippet=snippet,
                confidence=0.8,
            )
            count += 1

    session.commit()
    logger.info("pathway_extractor drug_id=%s mentions_created=%d", drug_id, count)
    return count
