"""
openFDA ingestor.

Fetches:
  - Drug label sections: boxed_warnings, warnings, contraindications,
    adverse_reactions, precautions
  - FAERS adverse event signals (top terms)

Stores into:
  label_warning, adverse_event, evidence(label/tox)
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache import get_or_fetch
from app.config import get_settings
from app.models import AdverseEvent, Evidence, LabelWarning
from app.services.base import BaseIngestor, DrugContext, NormalizedRecord

logger = logging.getLogger(__name__)


LABEL_SECTIONS = [
    "boxed_warning",
    "warnings",
    "contraindications",
    "adverse_reactions",
    "precautions",
    "warnings_and_cautions",
]


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _first_text(val: Any) -> str | None:
    """openFDA label fields are often lists of strings."""
    if val is None:
        return None
    if isinstance(val, list):
        return " ".join(str(v) for v in val[:3]).strip() or None
    return str(val).strip() or None


class OpenFDAIngestor(BaseIngestor):
    name = "openfda"

    def fetch(self, ctx: DrugContext) -> dict:
        settings = get_settings()
        base = settings.openfda_base_url
        result: dict = {"labels": [], "faers": []}

        # Try multiple search terms for label lookup
        search_terms = ctx.all_search_terms()[:6]

        label_found = False
        for term in search_terms:
            if label_found:
                break
            for field in ["openfda.generic_name", "openfda.brand_name", "openfda.substance_name"]:
                try:
                    url = f"{base}/label.json"
                    params = {
                        "search": f'{field}:"{term}"',
                        "limit": 3,
                    }
                    data = get_or_fetch(self.name, url, params)
                    if isinstance(data, dict) and data.get("results"):
                        result["labels"].extend(data["results"][:3])
                        label_found = True
                        break
                except Exception as exc:
                    logger.debug("openfda_label_miss field=%s term=%s err=%s", field, term, exc)

        # FAERS adverse event signals
        for term in search_terms[:3]:
            try:
                url = f"{base}/event.json"
                params = {
                    "search": f'patient.drug.medicinalproduct:"{term}"',
                    "count": "patient.reaction.reactionmeddrapt.exact",
                    "limit": 20,
                }
                data = get_or_fetch(f"{self.name}_faers", url, params)
                if isinstance(data, dict) and data.get("results"):
                    result["faers"].extend(data["results"][:20])
                    break
            except Exception as exc:
                logger.debug("openfda_faers_miss term=%s err=%s", term, exc)

        return result

    def parse(self, ctx: DrugContext, payload: dict) -> list[NormalizedRecord]:
        records: list[NormalizedRecord] = []
        labels: list[dict] = payload.get("labels", [])
        faers: list[dict] = payload.get("faers", [])

        # --- Label sections ---
        for label in labels:
            label_url = None
            openfda = label.get("openfda", {})
            set_id = label.get("set_id", "")
            if set_id:
                label_url = f"https://api.fda.gov/drug/label.json?search=set_id:{set_id}"

            for section in LABEL_SECTIONS:
                text = _first_text(label.get(section))
                if not text:
                    continue
                records.append(NormalizedRecord(
                    record_type="label_warning",
                    data={
                        "source": "openfda",
                        "section": section,
                        "text": text,
                        "url": label_url,
                    },
                    evidence={
                        "source": "openfda",
                        "evidence_type": "label",
                        "title": f"Label section: {section}",
                        "snippet_text": text[:512],
                        "url": label_url,
                        "metadata_json": {
                            "section": section,
                            "set_id": set_id,
                            "openfda_fields": {
                                "generic_name": openfda.get("generic_name"),
                                "brand_name": openfda.get("brand_name"),
                            },
                        },
                    },
                ))

        # --- FAERS adverse events ---
        for event in faers:
            term = event.get("term")
            count = event.get("count")
            if not term:
                continue

            # Rough seriousness heuristic based on term keywords
            term_lower = term.lower()
            if any(k in term_lower for k in ("death", "fatal", "cardiac arrest", "anaphyla")):
                seriousness = "high"
            elif any(k in term_lower for k in ("hospitali", "serious", "severe")):
                seriousness = "moderate"
            else:
                seriousness = "low"

            records.append(NormalizedRecord(
                record_type="adverse_event",
                data={
                    "source": "openfda",
                    "event_term": str(term)[:512],
                    "seriousness": seriousness,
                    "frequency": str(count) if count is not None else None,
                    "metadata_json": {"faers_count": count},
                },
                evidence={
                    "source": "openfda",
                    "evidence_type": "tox",
                    "title": f"FAERS signal: {term}",
                    "snippet_text": f"Reported {count} times in FAERS",
                    "url": None,
                    "metadata_json": {"faers_term": term, "count": count},
                },
            ))

        return records

    def upsert(self, session: Session, ctx: DrugContext, records: list[NormalizedRecord]) -> None:
        drug_id = ctx.drug_id

        for rec in records:
            if rec.record_type == "label_warning":
                d = rec.data
                section = d["section"]
                source = d.get("source", "openfda")
                exists = session.execute(
                    select(LabelWarning.id).where(
                        LabelWarning.drug_id == drug_id,
                        LabelWarning.section == section,
                        LabelWarning.source == source,
                    ).limit(1)
                ).first()
                if exists:
                    continue
                session.add(LabelWarning(
                    id=_new_uuid(),
                    drug_id=drug_id,
                    source=source,
                    section=section,
                    text=d.get("text"),
                    url=d.get("url"),
                ))

            elif rec.record_type == "adverse_event":
                d = rec.data
                session.add(AdverseEvent(
                    id=_new_uuid(),
                    drug_id=drug_id,
                    source=d.get("source", "openfda"),
                    event_term=d["event_term"],
                    seriousness=d.get("seriousness"),
                    frequency=d.get("frequency"),
                    metadata_json=d.get("metadata_json"),
                ))

            if rec.evidence:
                ev = rec.evidence
                session.add(Evidence(
                    id=_new_uuid(), drug_id=drug_id,
                    source=ev.get("source", "openfda"),
                    evidence_type=ev.get("evidence_type", "label"),
                    title=ev.get("title"),
                    snippet_text=ev.get("snippet_text"),
                    url=ev.get("url"),
                    metadata_json=ev.get("metadata_json"),
                ))
