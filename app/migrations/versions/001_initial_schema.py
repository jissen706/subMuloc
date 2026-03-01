"""initial schema

Revision ID: 001_initial_schema
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # drug
    op.create_table(
        "drug",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("canonical_name", sa.String(512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_name"),
    )
    op.create_index("ix_drug_canonical_name", "drug", ["canonical_name"])

    # drug_identifier
    op.create_table(
        "drug_identifier",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("id_type", sa.String(64), nullable=False),
        sa.Column("value", sa.String(2048), nullable=False),
        sa.ForeignKeyConstraint(["drug_id"], ["drug.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drug_id", "id_type", "value", name="uq_drug_id_type_value"),
    )
    op.create_index("ix_drug_identifier_drug_id", "drug_identifier", ["drug_id"])

    # drug_synonym
    op.create_table(
        "drug_synonym",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("synonym", sa.String(512), nullable=False),
        sa.ForeignKeyConstraint(["drug_id"], ["drug.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drug_id", "synonym", name="uq_drug_synonym"),
    )
    op.create_index("ix_drug_synonym_drug_id", "drug_synonym", ["drug_id"])
    op.create_index("ix_drug_synonym_synonym", "drug_synonym", ["synonym"])

    # molecular_structure
    op.create_table(
        "molecular_structure",
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("smiles", sa.Text(), nullable=True),
        sa.Column("inchi", sa.Text(), nullable=True),
        sa.Column("molecular_formula", sa.String(256), nullable=True),
        sa.Column("molecular_weight", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["drug_id"], ["drug.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("drug_id"),
    )

    # target
    op.create_table(
        "target",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("target_name", sa.String(512), nullable=True),
        sa.Column("gene_symbol", sa.String(128), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("evidence", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["drug_id"], ["drug.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drug_id", "target_name", "gene_symbol", "source", name="uq_target"),
    )
    op.create_index("ix_target_drug_id", "target", ["drug_id"])

    # trial
    op.create_table(
        "trial",
        sa.Column("nct_id", sa.String(16), nullable=False),
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("phase", sa.String(64), nullable=True),
        sa.Column("status", sa.String(64), nullable=True),
        sa.Column("conditions_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("sponsor", sa.String(512), nullable=True),
        sa.Column("start_date", sa.String(32), nullable=True),
        sa.Column("completion_date", sa.String(32), nullable=True),
        sa.Column("results_posted", sa.Boolean(), nullable=True),
        sa.Column("url", sa.String(512), nullable=True),
        sa.Column("raw_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["drug_id"], ["drug.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("nct_id"),
    )
    op.create_index("ix_trial_drug_id", "trial", ["drug_id"])

    # publication
    op.create_table(
        "publication",
        sa.Column("pmid", sa.String(32), nullable=False),
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("journal", sa.String(512), nullable=True),
        sa.Column("authors_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("url", sa.String(512), nullable=True),
        sa.Column("raw_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["drug_id"], ["drug.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("pmid"),
    )
    op.create_index("ix_publication_drug_id", "publication", ["drug_id"])

    # label_warning
    op.create_table(
        "label_warning",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("section", sa.String(128), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("url", sa.String(512), nullable=True),
        sa.ForeignKeyConstraint(["drug_id"], ["drug.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_label_warning_drug_id", "label_warning", ["drug_id"])

    # adverse_event
    op.create_table(
        "adverse_event",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("event_term", sa.String(512), nullable=False),
        sa.Column("seriousness", sa.String(32), nullable=True),
        sa.Column("frequency", sa.String(128), nullable=True),
        sa.Column("metadata_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["drug_id"], ["drug.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_adverse_event_drug_id", "adverse_event", ["drug_id"])

    # clinvar_association
    op.create_table(
        "clinvar_association",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("gene_symbol", sa.String(128), nullable=True),
        sa.Column("variant", sa.String(512), nullable=True),
        sa.Column("clinical_significance", sa.String(256), nullable=True),
        sa.Column("condition", sa.Text(), nullable=True),
        sa.Column("url", sa.String(512), nullable=True),
        sa.Column("raw_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["drug_id"], ["drug.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_clinvar_association_drug_id", "clinvar_association", ["drug_id"])

    # toxicity_metric
    op.create_table(
        "toxicity_metric",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("metric_type", sa.String(128), nullable=False),
        sa.Column("value", sa.String(256), nullable=True),
        sa.Column("units", sa.String(64), nullable=True),
        sa.Column("interpreted_flag", sa.String(32), nullable=True),
        sa.Column("evidence_source", sa.String(64), nullable=True),
        sa.Column("evidence_ref", sa.String(256), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["drug_id"], ["drug.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_toxicity_metric_drug_id", "toxicity_metric", ["drug_id"])

    # disease_pathway_mention
    op.create_table(
        "disease_pathway_mention",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("disease_name", sa.String(512), nullable=True),
        sa.Column("pathway_term", sa.String(256), nullable=False),
        sa.Column("evidence_source", sa.String(64), nullable=False),
        sa.Column("evidence_ref", sa.String(256), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["drug_id"], ["drug.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_disease_pathway_mention_drug_id", "disease_pathway_mention", ["drug_id"])

    # evidence (generic)
    op.create_table(
        "evidence",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("evidence_type", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("snippet_text", sa.Text(), nullable=True),
        sa.Column("url", sa.String(512), nullable=True),
        sa.Column("metadata_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["drug_id"], ["drug.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_drug_id", "evidence", ["drug_id"])


def downgrade() -> None:
    op.drop_table("evidence")
    op.drop_table("disease_pathway_mention")
    op.drop_table("toxicity_metric")
    op.drop_table("clinvar_association")
    op.drop_table("adverse_event")
    op.drop_table("label_warning")
    op.drop_table("publication")
    op.drop_table("trial")
    op.drop_table("target")
    op.drop_table("molecular_structure")
    op.drop_table("drug_synonym")
    op.drop_table("drug_identifier")
    op.drop_table("drug")
