"""
Persistence helpers for MechanismVector (Block 2).

upsert_mechanism_vector: insert or update a single row keyed by
(entity_type, entity_id, vocab_version, nodes_hash).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MechanismVector


def upsert_mechanism_vector(
    entity_type: str,
    entity_id: str,
    payload: dict,
    db: Session,
) -> MechanismVector:
    """
    Insert or update MechanismVector for the given entity.

    payload keys required:
      vocab_version, nodes_hash, dense_weights, dense_direction, sparse

    Returns the persisted (and refreshed) MechanismVector instance.
    """
    existing = db.execute(
        select(MechanismVector).where(
            MechanismVector.entity_type == entity_type,
            MechanismVector.entity_id == entity_id,
            MechanismVector.vocab_version == payload["vocab_version"],
            MechanismVector.nodes_hash == payload["nodes_hash"],
        )
    ).scalars().first()

    if existing is not None:
        # Update in-place — SQLAlchemy onupdate fires on commit
        existing.dense_weights = payload["dense_weights"]
        existing.dense_direction = payload["dense_direction"]
        existing.sparse = payload["sparse"]
        db.flush()
        db.refresh(existing)
        return existing

    mv = MechanismVector(
        entity_type=entity_type,
        entity_id=entity_id,
        vocab_version=payload["vocab_version"],
        nodes_hash=payload["nodes_hash"],
        dense_weights=payload["dense_weights"],
        dense_direction=payload["dense_direction"],
        sparse=payload["sparse"],
    )
    db.add(mv)
    db.flush()
    db.refresh(mv)
    return mv
