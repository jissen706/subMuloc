"""pair_evidence table (Block 4)

Revision ID: 004_pair_evidence
Revises: 003_mechanism_vector
Create Date: 2024-01-04 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "004_pair_evidence"
down_revision = "003_mechanism_vector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pair_evidence",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("drug_id", sa.String(36), nullable=False),
        sa.Column("disease_id", sa.String(36), nullable=False),
        sa.Column("score_version", sa.String(64), nullable=False, server_default="score_v1"),
        sa.Column("payload", JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.UniqueConstraint(
            "drug_id", "disease_id", "score_version",
            name="uq_pair_evidence_drug_disease_version",
        ),
    )
    op.create_index("ix_pair_evidence_drug_id", "pair_evidence", ["drug_id"])
    op.create_index("ix_pair_evidence_disease_id", "pair_evidence", ["disease_id"])


def downgrade() -> None:
    op.drop_index("ix_pair_evidence_disease_id", table_name="pair_evidence")
    op.drop_index("ix_pair_evidence_drug_id", table_name="pair_evidence")
    op.drop_table("pair_evidence")
