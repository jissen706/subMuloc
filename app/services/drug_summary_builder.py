"""
Shared helper: build drug_short_v1 dict from DB without going through the
FastAPI route (avoids circular import between main.py and routes/vectorize.py).

Block 1 compaction logic is NOT modified here — we simply call
summary_compactor.compact_drug_summary on the raw DrugSummaryOut payload.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ClinVarAssociation,
    DiseasePathwayMention,
    Drug,
    DrugIdentifier,
    DrugSynonym,
    LabelWarning,
    MolecularStructure,
    Publication,
    Target,
    ToxicityMetric,
    Trial,
)
from app.schemas.summary import (
    ClinVarAssociationOut,
    DrugSummaryOut,
    IdentifierOut,
    LabelWarningOut,
    MolecularStructureOut,
    PathwayMentionOut,
    PublicationSummaryOut,
    TargetOut,
    ToxicityMetricOut,
    TrialOut,
    TrialSummaryOut,
)
from app.services.summary_compactor import compact_drug_summary


def build_drug_short(drug_id: str, db: Session) -> dict | None:
    """
    Query the DB, build DrugSummaryOut, and compact to drug_short_v1 dict.
    Returns None if the drug_id does not exist.
    This is the same logic as main._build_drug_summary + compact_drug_summary.
    """
    drug = db.get(Drug, drug_id)
    if drug is None:
        return None

    identifiers = db.execute(
        select(DrugIdentifier).where(DrugIdentifier.drug_id == drug_id)
    ).scalars().all()

    synonyms_rows = db.execute(
        select(DrugSynonym).where(DrugSynonym.drug_id == drug_id)
    ).scalars().all()

    structure = db.get(MolecularStructure, drug_id)

    targets = db.execute(
        select(Target).where(Target.drug_id == drug_id)
    ).scalars().all()

    trials = db.execute(
        select(Trial).where(Trial.drug_id == drug_id)
    ).scalars().all()

    pubs = db.execute(
        select(Publication).where(Publication.drug_id == drug_id)
    ).scalars().all()

    warnings = db.execute(
        select(LabelWarning).where(LabelWarning.drug_id == drug_id)
    ).scalars().all()

    tox_metrics = db.execute(
        select(ToxicityMetric).where(ToxicityMetric.drug_id == drug_id)
    ).scalars().all()

    pathway_rows = db.execute(
        select(DiseasePathwayMention).where(DiseasePathwayMention.drug_id == drug_id)
    ).scalars().all()

    clinvar_rows = db.execute(
        select(ClinVarAssociation).where(ClinVarAssociation.drug_id == drug_id)
    ).scalars().all()

    # Trials summary
    by_phase: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    trial_outs: list[TrialOut] = []
    for t in trials:
        by_phase[t.phase or "Unknown"] += 1
        by_status[t.status or "Unknown"] += 1
        trial_outs.append(TrialOut(
            nct_id=t.nct_id,
            title=t.title,
            phase=t.phase,
            status=t.status,
            conditions=t.conditions_json if isinstance(t.conditions_json, list) else [],
            sponsor=t.sponsor,
            start_date=t.start_date,
            completion_date=t.completion_date,
            results_posted=t.results_posted,
            url=t.url,
        ))

    trial_summary = TrialSummaryOut(
        total=len(trials),
        by_phase=dict(by_phase),
        by_status=dict(by_status),
        trials=trial_outs,
    )

    # Publications summary
    by_year: dict[int, int] = defaultdict(int)
    for p in pubs:
        if p.year:
            by_year[p.year] += 1

    recent_pubs = sorted(
        [p for p in pubs if p.year],
        key=lambda p: p.year or 0,
        reverse=True,
    )[:10]

    pub_summary = PublicationSummaryOut(
        total=len(pubs),
        by_year=dict(by_year),
        recent=[
            {"pmid": p.pmid, "title": p.title, "year": p.year,
             "journal": p.journal, "url": p.url}
            for p in recent_pubs
        ],
    )

    # Pathway mentions: aggregate by term
    pathway_agg: dict[str, dict] = {}
    for row in pathway_rows:
        term = row.pathway_term
        if term not in pathway_agg:
            pathway_agg[term] = {"count": 0, "max_confidence": 0.0, "sources": set()}
        pathway_agg[term]["count"] += 1
        pathway_agg[term]["max_confidence"] = max(
            pathway_agg[term]["max_confidence"], row.confidence or 0.0
        )
        pathway_agg[term]["sources"].add(row.evidence_source)

    pathway_out = sorted(
        [
            PathwayMentionOut(
                pathway_term=term,
                count=agg["count"],
                max_confidence=round(agg["max_confidence"], 2),
                evidence_sources=sorted(agg["sources"]),
            )
            for term, agg in pathway_agg.items()
        ],
        key=lambda x: (-x.count, -x.max_confidence),
    )

    summary_out = DrugSummaryOut(
        drug_id=drug_id,
        canonical_name=drug.canonical_name,
        identifiers=[IdentifierOut(id_type=i.id_type, value=i.value) for i in identifiers],
        synonyms=[s.synonym for s in synonyms_rows],
        molecular_structure=MolecularStructureOut(
            smiles=structure.smiles if structure else None,
            inchi=structure.inchi if structure else None,
            molecular_formula=structure.molecular_formula if structure else None,
            molecular_weight=structure.molecular_weight if structure else None,
        ) if structure else None,
        targets=[
            TargetOut(
                target_name=t.target_name,
                gene_symbol=t.gene_symbol,
                source=t.source,
                evidence=t.evidence,
            )
            for t in targets
        ],
        trials=trial_summary,
        publications=pub_summary,
        label_warnings=[
            LabelWarningOut(section=w.section, text=w.text, url=w.url)
            for w in warnings
        ],
        toxicity_metrics=[
            ToxicityMetricOut(
                metric_type=m.metric_type,
                value=m.value,
                units=m.units,
                interpreted_flag=m.interpreted_flag,
                evidence_source=m.evidence_source,
                evidence_ref=m.evidence_ref,
                notes=m.notes,
            )
            for m in tox_metrics
        ],
        pathway_mentions=pathway_out,
        clinvar_associations=[
            ClinVarAssociationOut(
                gene_symbol=c.gene_symbol,
                variant=c.variant,
                clinical_significance=c.clinical_significance,
                condition=c.condition,
                url=c.url,
            )
            for c in clinvar_rows
        ],
    )

    return compact_drug_summary(summary_out.model_dump())
