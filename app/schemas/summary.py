"""
Response schema for GET /drug/{id}/summary.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class IdentifierOut(BaseModel):
    id_type: str
    value: str


class MolecularStructureOut(BaseModel):
    smiles: str | None
    inchi: str | None
    molecular_formula: str | None
    molecular_weight: float | None


class TargetOut(BaseModel):
    target_name: str | None
    gene_symbol: str | None
    source: str
    evidence: Any | None


class TrialOut(BaseModel):
    nct_id: str
    title: str | None
    phase: str | None
    status: str | None
    conditions: list[str]
    sponsor: str | None
    start_date: str | None
    completion_date: str | None
    results_posted: bool | None
    url: str | None


class TrialSummaryOut(BaseModel):
    total: int
    by_phase: dict[str, int]
    by_status: dict[str, int]
    trials: list[TrialOut]


class PublicationSummaryOut(BaseModel):
    total: int
    by_year: dict[int, int]
    recent: list[dict[str, Any]]  # top 10 most recent


class LabelWarningOut(BaseModel):
    section: str
    text: str | None
    url: str | None


class ToxicityMetricOut(BaseModel):
    metric_type: str
    value: str | None
    units: str | None
    interpreted_flag: str | None
    evidence_source: str | None
    evidence_ref: str | None
    notes: str | None


class PathwayMentionOut(BaseModel):
    pathway_term: str
    count: int
    max_confidence: float
    evidence_sources: list[str]


class ClinVarAssociationOut(BaseModel):
    gene_symbol: str | None
    variant: str | None
    clinical_significance: str | None
    condition: str | None
    url: str | None


class DrugSummaryOut(BaseModel):
    drug_id: str
    canonical_name: str
    identifiers: list[IdentifierOut]
    synonyms: list[str]
    molecular_structure: MolecularStructureOut | None
    targets: list[TargetOut]
    trials: TrialSummaryOut
    publications: PublicationSummaryOut
    label_warnings: list[LabelWarningOut]
    toxicity_metrics: list[ToxicityMetricOut]
    pathway_mentions: list[PathwayMentionOut]
    clinvar_associations: list[ClinVarAssociationOut]
