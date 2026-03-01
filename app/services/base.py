"""
Shared base types for the ingestion pipeline.

DrugContext   - carries resolved drug identifiers through the pipeline
NormalizedRecord - lightweight wrapper returned by each parser
SourceIngestor   - Protocol that every source module must satisfy
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# DrugContext
# ---------------------------------------------------------------------------
@dataclass
class DrugContext:
    """
    Resolved drug identity.  Populated incrementally:
      1. resolver.py fills canonical_name + initial synonyms + identifiers
      2. Each ingestor may ADD to synonyms / identifiers as it discovers new ones.
    """

    drug_id: str                              # UUID primary key in `drug` table
    canonical_name: str
    input_name: str                           # original user-supplied string
    synonyms: set[str] = field(default_factory=set)

    # Key/value bag: id_type → value
    # e.g. {"pubchem_cid": "5284616", "chembl_id": "CHEMBL413"}
    identifiers: dict[str, str] = field(default_factory=dict)

    def pubchem_cid(self) -> str | None:
        return self.identifiers.get("pubchem_cid")

    def chembl_id(self) -> str | None:
        return self.identifiers.get("chembl_id")

    def all_search_terms(self) -> list[str]:
        """
        Return deduplicated search terms, prioritizing the most useful names first:
        input_name and canonical_name come first (short, recognizable), then
        synonyms sorted by length (shorter = more useful for API searches).
        """
        priority = []
        seen: set[str] = set()
        for t in [self.input_name, self.canonical_name]:
            if t and t not in seen:
                priority.append(t)
                seen.add(t)
        rest = sorted(
            (s for s in self.synonyms if s not in seen),
            key=lambda s: (len(s), s),
        )
        return priority + rest


# ---------------------------------------------------------------------------
# NormalizedRecord
# ---------------------------------------------------------------------------
@dataclass
class NormalizedRecord:
    """
    Generic container passed from parse() → upsert().
    Each ingestor may use only the fields relevant to it;
    the rest stay None.
    """

    record_type: str                          # 'structure' | 'identifier' | 'synonym' | 'target'
                                             # | 'trial' | 'publication' | 'label_warning'
                                             # | 'adverse_event' | 'clinvar' | 'evidence'
    data: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] | None = None   # if set, also upsert into evidence table


# ---------------------------------------------------------------------------
# SourceIngestor Protocol
# ---------------------------------------------------------------------------
@runtime_checkable
class SourceIngestor(Protocol):
    name: str

    def fetch(self, ctx: DrugContext) -> dict:
        """
        Pull raw payload from the external source.
        Use cache.get_or_fetch() internally.
        Return a dict that will be passed unchanged to parse().
        """
        ...

    def parse(self, ctx: DrugContext, payload: dict) -> list[NormalizedRecord]:
        """
        Convert raw payload into a list of NormalizedRecord objects.
        Must be pure – no DB or network calls.
        """
        ...

    def upsert(self, session: Session, ctx: DrugContext, records: list[NormalizedRecord]) -> None:
        """
        Persist records into the DB.
        All writes must be idempotent (use merge/on_conflict_do_nothing).
        """
        ...

    def run(self, session: Session, ctx: DrugContext) -> list[NormalizedRecord]:
        """
        Convenience: fetch → parse → upsert in one call.
        Default implementation provided below; override only if needed.
        """
        ...


# ---------------------------------------------------------------------------
# BaseIngestor – concrete mixin providing default run()
# ---------------------------------------------------------------------------
class BaseIngestor:
    """
    Inherit from this to get a free run() implementation.
    Subclasses only need to implement fetch / parse / upsert.
    """

    name: str = "base"

    def fetch(self, ctx: DrugContext) -> dict:
        raise NotImplementedError

    def parse(self, ctx: DrugContext, payload: dict) -> list[NormalizedRecord]:
        raise NotImplementedError

    def upsert(self, session: Session, ctx: DrugContext, records: list[NormalizedRecord]) -> None:
        raise NotImplementedError

    def run(self, session: Session, ctx: DrugContext) -> list[NormalizedRecord]:
        import logging
        logger = logging.getLogger(f"ingestor.{self.name}")
        try:
            payload = self.fetch(ctx)
            records = self.parse(ctx, payload)
            self.upsert(session, ctx, records)
            session.commit()
            logger.info("ingestor=%s drug=%s records=%d", self.name, ctx.canonical_name, len(records))
            return records
        except Exception as exc:
            session.rollback()
            logger.error("ingestor=%s drug=%s error=%s", self.name, ctx.canonical_name, exc, exc_info=True)
            return []
