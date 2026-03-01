"""disease tables (Block 1)

Revision ID: 002_disease_tables
Revises: 001_initial_schema
Create Date: 2024-01-02 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "002_disease_tables"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "disease",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("ids_json", sa.JSON(), nullable=True),
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
    )
    op.create_index("ix_disease_canonical_name", "disease", ["canonical_name"])

    op.create_table(
        "disease_artifact",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("disease_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["disease_id"], ["disease.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_disease_artifact_disease_id", "disease_artifact", ["disease_id"])


def downgrade() -> None:
    op.drop_table("disease_artifact")
    op.drop_table("disease")
