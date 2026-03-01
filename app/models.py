from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# drug
# ---------------------------------------------------------------------------
class Drug(Base):
    __tablename__ = "drug"

    id = Column(String(36), primary_key=True, default=_uuid)
    canonical_name = Column(String(512), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    identifiers = relationship("DrugIdentifier", back_populates="drug", cascade="all, delete-orphan")
    synonyms = relationship("DrugSynonym", back_populates="drug", cascade="all, delete-orphan")
    molecular_structure = relationship("MolecularStructure", back_populates="drug", uselist=False, cascade="all, delete-orphan")
    targets = relationship("Target", back_populates="drug", cascade="all, delete-orphan")
    trials = relationship("Trial", back_populates="drug", cascade="all, delete-orphan")
    publications = relationship("Publication", back_populates="drug", cascade="all, delete-orphan")
    label_warnings = relationship("LabelWarning", back_populates="drug", cascade="all, delete-orphan")
    adverse_events = relationship("AdverseEvent", back_populates="drug", cascade="all, delete-orphan")
    clinvar_associations = relationship("ClinVarAssociation", back_populates="drug", cascade="all, delete-orphan")
    toxicity_metrics = relationship("ToxicityMetric", back_populates="drug", cascade="all, delete-orphan")
    disease_pathway_mentions = relationship("DiseasePathwayMention", back_populates="drug", cascade="all, delete-orphan")
    evidence_records = relationship("Evidence", back_populates="drug", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# drug_identifier
# ---------------------------------------------------------------------------
class DrugIdentifier(Base):
    __tablename__ = "drug_identifier"
    __table_args__ = (UniqueConstraint("drug_id", "id_type", "value", name="uq_drug_id_type_value"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    drug_id = Column(String(36), ForeignKey("drug.id", ondelete="CASCADE"), nullable=False, index=True)
    id_type = Column(String(64), nullable=False)  # chembl_id | pubchem_cid | cas | inchi | smiles | unii | drugbank_optional
    value = Column(String(2048), nullable=False)

    drug = relationship("Drug", back_populates="identifiers")


# ---------------------------------------------------------------------------
# drug_synonym
# ---------------------------------------------------------------------------
class DrugSynonym(Base):
    __tablename__ = "drug_synonym"
    __table_args__ = (UniqueConstraint("drug_id", "synonym", name="uq_drug_synonym"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    drug_id = Column(String(36), ForeignKey("drug.id", ondelete="CASCADE"), nullable=False, index=True)
    synonym = Column(String(512), nullable=False, index=True)

    drug = relationship("Drug", back_populates="synonyms")


# ---------------------------------------------------------------------------
# molecular_structure
# ---------------------------------------------------------------------------
class MolecularStructure(Base):
    __tablename__ = "molecular_structure"

    drug_id = Column(String(36), ForeignKey("drug.id", ondelete="CASCADE"), primary_key=True)
    smiles = Column(Text, nullable=True)
    inchi = Column(Text, nullable=True)
    molecular_formula = Column(String(256), nullable=True)
    molecular_weight = Column(Float, nullable=True)

    drug = relationship("Drug", back_populates="molecular_structure")


# ---------------------------------------------------------------------------
# target
# ---------------------------------------------------------------------------
class Target(Base):
    __tablename__ = "target"
    __table_args__ = (
        UniqueConstraint("drug_id", "target_name", "gene_symbol", "source", name="uq_target"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    drug_id = Column(String(36), ForeignKey("drug.id", ondelete="CASCADE"), nullable=False, index=True)
    target_name = Column(String(512), nullable=True)
    gene_symbol = Column(String(128), nullable=True)
    source = Column(String(64), nullable=False)  # chembl | literature
    evidence = Column(JSON, nullable=True)

    drug = relationship("Drug", back_populates="targets")


# ---------------------------------------------------------------------------
# trial
# ---------------------------------------------------------------------------
class Trial(Base):
    __tablename__ = "trial"

    nct_id = Column(String(16), primary_key=True)
    drug_id = Column(String(36), ForeignKey("drug.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(Text, nullable=True)
    phase = Column(String(64), nullable=True)
    status = Column(String(64), nullable=True)
    conditions_json = Column(JSON, nullable=True)
    sponsor = Column(String(512), nullable=True)
    start_date = Column(String(32), nullable=True)
    completion_date = Column(String(32), nullable=True)
    results_posted = Column(Boolean, nullable=True)
    url = Column(String(512), nullable=True)
    raw_json = Column(JSON, nullable=True)

    drug = relationship("Drug", back_populates="trials")


# ---------------------------------------------------------------------------
# publication
# ---------------------------------------------------------------------------
class Publication(Base):
    __tablename__ = "publication"

    pmid = Column(String(32), primary_key=True)
    drug_id = Column(String(36), ForeignKey("drug.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(Text, nullable=True)
    abstract = Column(Text, nullable=True)
    year = Column(Integer, nullable=True)
    journal = Column(String(512), nullable=True)
    authors_json = Column(JSON, nullable=True)
    url = Column(String(512), nullable=True)
    raw_json = Column(JSON, nullable=True)

    drug = relationship("Drug", back_populates="publications")


# ---------------------------------------------------------------------------
# label_warning
# ---------------------------------------------------------------------------
class LabelWarning(Base):
    __tablename__ = "label_warning"

    id = Column(String(36), primary_key=True, default=_uuid)
    drug_id = Column(String(36), ForeignKey("drug.id", ondelete="CASCADE"), nullable=False, index=True)
    source = Column(String(64), nullable=False)  # openfda
    section = Column(String(128), nullable=False)  # boxed_warning | warnings | contraindications | adverse_reactions | precautions
    text = Column(Text, nullable=True)
    url = Column(String(512), nullable=True)

    drug = relationship("Drug", back_populates="label_warnings")


# ---------------------------------------------------------------------------
# adverse_event
# ---------------------------------------------------------------------------
class AdverseEvent(Base):
    __tablename__ = "adverse_event"

    id = Column(String(36), primary_key=True, default=_uuid)
    drug_id = Column(String(36), ForeignKey("drug.id", ondelete="CASCADE"), nullable=False, index=True)
    source = Column(String(64), nullable=False)  # openfda | ctgov
    event_term = Column(String(512), nullable=False)
    seriousness = Column(String(32), nullable=True)  # low|moderate|high|unknown
    frequency = Column(String(128), nullable=True)
    metadata_json = Column(JSON, nullable=True)

    drug = relationship("Drug", back_populates="adverse_events")


# ---------------------------------------------------------------------------
# clinvar_association
# ---------------------------------------------------------------------------
class ClinVarAssociation(Base):
    __tablename__ = "clinvar_association"

    id = Column(String(36), primary_key=True, default=_uuid)
    drug_id = Column(String(36), ForeignKey("drug.id", ondelete="CASCADE"), nullable=False, index=True)
    gene_symbol = Column(String(128), nullable=True)
    variant = Column(String(512), nullable=True)
    clinical_significance = Column(String(256), nullable=True)
    condition = Column(Text, nullable=True)
    url = Column(String(512), nullable=True)
    raw_json = Column(JSON, nullable=True)

    drug = relationship("Drug", back_populates="clinvar_associations")


# ---------------------------------------------------------------------------
# toxicity_metric
# ---------------------------------------------------------------------------
class ToxicityMetric(Base):
    __tablename__ = "toxicity_metric"

    id = Column(String(36), primary_key=True, default=_uuid)
    drug_id = Column(String(36), ForeignKey("drug.id", ondelete="CASCADE"), nullable=False, index=True)
    metric_type = Column(String(128), nullable=False)  # Cmax | Vd | ED50 | LD50 | MTD | AUC | ALT_elevation | SAE_rate | DLT_flag | QT_prolongation
    value = Column(String(256), nullable=True)
    units = Column(String(64), nullable=True)
    interpreted_flag = Column(String(32), nullable=True)  # safe | concerning | unknown
    evidence_source = Column(String(64), nullable=True)  # ctgov | openfda | pubmed
    evidence_ref = Column(String(256), nullable=True)  # nct_id | pmid | label_section
    notes = Column(Text, nullable=True)

    drug = relationship("Drug", back_populates="toxicity_metrics")


# ---------------------------------------------------------------------------
# disease_pathway_mention
# ---------------------------------------------------------------------------
class DiseasePathwayMention(Base):
    __tablename__ = "disease_pathway_mention"

    id = Column(String(36), primary_key=True, default=_uuid)
    drug_id = Column(String(36), ForeignKey("drug.id", ondelete="CASCADE"), nullable=False, index=True)
    disease_name = Column(String(512), nullable=True)
    pathway_term = Column(String(256), nullable=False)
    evidence_source = Column(String(64), nullable=False)  # pubmed | chembl | ctgov
    evidence_ref = Column(String(256), nullable=True)  # pmid | nct_id | chembl_id
    snippet = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)  # 0-1 heuristic

    drug = relationship("Drug", back_populates="disease_pathway_mentions")


# ---------------------------------------------------------------------------
# evidence  (generic, future-proof)
# ---------------------------------------------------------------------------
class Evidence(Base):
    __tablename__ = "evidence"

    id = Column(String(36), primary_key=True, default=_uuid)
    drug_id = Column(String(36), ForeignKey("drug.id", ondelete="CASCADE"), nullable=False, index=True)
    source = Column(String(64), nullable=False)
    evidence_type = Column(String(64), nullable=False)  # trial | paper | target | tox | label | variant | pathway | structure
    title = Column(Text, nullable=True)
    snippet_text = Column(Text, nullable=True)
    url = Column(String(512), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    drug = relationship("Drug", back_populates="evidence_records")


# ---------------------------------------------------------------------------
# disease (Block 1: disease ingestion)
# ---------------------------------------------------------------------------
class Disease(Base):
    __tablename__ = "disease"

    id = Column(String(36), primary_key=True, default=_uuid)
    canonical_name = Column(Text, nullable=False, index=True)
    ids_json = Column(JSON, nullable=True)  # {"orpha": str|null, "omim": str|null}
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    artifacts = relationship("DiseaseArtifact", back_populates="disease", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# disease_artifact
# ---------------------------------------------------------------------------
class DiseaseArtifact(Base):
    __tablename__ = "disease_artifact"

    id = Column(String(36), primary_key=True, default=_uuid)
    disease_id = Column(String(36), ForeignKey("disease.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(64), nullable=False)  # summary_raw
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    disease = relationship("Disease", back_populates="artifacts")


# ---------------------------------------------------------------------------
# mechanism_vector  (Block 2)
# Stores dense + sparse mechanism vectors for drug/disease entities.
# Unique on (entity_type, entity_id, vocab_version, nodes_hash) so a
# vocab bump automatically creates new rows rather than overwriting.
# ---------------------------------------------------------------------------
class MechanismVector(Base):
    __tablename__ = "mechanism_vector"
    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_id", "vocab_version", "nodes_hash",
            name="uq_mechvec_entity_vocab",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    entity_type = Column(String(16), nullable=False)   # 'drug' | 'disease'
    entity_id = Column(String(36), nullable=False, index=True)
    vocab_version = Column(String(64), nullable=False)
    nodes_hash = Column(String(128), nullable=False)
    dense_weights = Column(JSON, nullable=True)    # list[float], len == N nodes
    dense_direction = Column(JSON, nullable=True)  # list[int],  len == N nodes
    sparse = Column(JSON, nullable=True)           # dict node -> {weight, direction, evidence}
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_utcnow,
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )
