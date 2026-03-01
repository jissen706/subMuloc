"""mechanism_vector table (Block 2)

Revision ID: 003_mechanism_vector
Revises: 002_disease_tables
Create Date: 2024-01-03 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003_mechanism_vector"
down_revision = "002_disease_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mechanism_vector",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column(
            "entity_type",
            sa.String(16),
            sa.CheckConstraint("entity_type IN ('drug','disease')", name="ck_mechvec_entity_type"),
            nullable=False,
        ),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("vocab_version", sa.String(64), nullable=False),
        sa.Column("nodes_hash", sa.String(128), nullable=False),
        sa.Column("dense_weights", sa.JSON(), nullable=True),
        sa.Column("dense_direction", sa.JSON(), nullable=True),
        sa.Column("sparse", sa.JSON(), nullable=True),
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
            "entity_type", "entity_id", "vocab_version", "nodes_hash",
            name="uq_mechvec_entity_vocab",
        ),
    )
    op.create_index("ix_mechanism_vector_entity_id", "mechanism_vector", ["entity_id"])
    op.create_index(
        "ix_mechanism_vector_entity_type_vocab",
        "mechanism_vector",
        ["entity_type", "vocab_version", "nodes_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_mechanism_vector_entity_type_vocab", table_name="mechanism_vector")
    op.drop_index("ix_mechanism_vector_entity_id", table_name="mechanism_vector")
    op.drop_table("mechanism_vector")
