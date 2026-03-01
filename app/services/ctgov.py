"""
ClinicalTrials.gov ingestor (v2 API).

Fetches:
  - Trial list by drug/intervention name across all synonyms
  - Full study JSON per NCT ID

Stores into:
  trial, adverse_event (if AE data in results), evidence(trial)
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.cache import get_or_fetch
from app.config import get_settings
from app.models import AdverseEvent, Evidence, Trial
from app.services.base import BaseIngestor, DrugContext, NormalizedRecord

logger = logging.getLogger(__name__)

TOXICITY_STOP_KEYWORDS = {
    "toxicity", "toxic", "adverse", "safety", "terminated", "overdose",
    "dose-limiting", "dlt", "fatal", "death", "serious adverse",
}


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _safe_str(val: Any, max_len: int = 512) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s[:max_len] if s else None


class CTGovIngestor(BaseIngestor):
    name = "ctgov"

    def fetch(self, ctx: DrugContext) -> dict:
        settings = get_settings()
        base = settings.ctgov_base_url
        max_results = settings.ctgov_max_results

        collected_nct_ids: set[str] = set()
        studies: dict[str, dict] = {}

        # Search across top synonyms (deduplicated, prefer shorter/more canonical names first)
        search_terms = sorted(ctx.all_search_terms(), key=lambda x: (len(x), x))[:10]

        for term in search_terms:
            try:
                url = f"{base}/studies"
                params = {
                    "query.intr": term,
                    "pageSize": min(max_results, 1000),
                    "format": "json",
                    "fields": "NCTId,BriefTitle,Phase,OverallStatus,Condition,LeadSponsorName,StartDate,CompletionDate,ResultsFirstPostDate",
                }
                data = get_or_fetch(self.name, url, params)
                if not isinstance(data, dict):
                    continue
                for study in data.get("studies", []):
                    ps = study.get("protocolSection", {})
                    id_mod = ps.get("identificationModule", {})
                    nct_id = id_mod.get("nctId")
                    if nct_id and nct_id not in collected_nct_ids:
                        collected_nct_ids.add(nct_id)
                        studies[nct_id] = study
            except Exception as exc:
                logger.warning("ctgov_search_err term=%s err=%s", term, exc)

        logger.info("ctgov_found drug=%s nct_count=%d", ctx.canonical_name, len(collected_nct_ids))

        # Fetch full study details for each NCT ID
        full_studies: dict[str, dict] = {}
        for nct_id in list(collected_nct_ids)[:max_results]:
            if nct_id in studies:
                # Already have the lightweight record; fetch full if needed
                full_studies[nct_id] = studies[nct_id]
            try:
                url = f"{base}/studies/{nct_id}"
                params = {"format": "json"}
                full_data = get_or_fetch(f"{self.name}_full", url, params)
                if isinstance(full_data, dict):
                    full_studies[nct_id] = full_data
            except Exception as exc:
                logger.debug("ctgov_full_err nct=%s err=%s", nct_id, exc)

        return {"studies": full_studies}

    def parse(self, ctx: DrugContext, payload: dict) -> list[NormalizedRecord]:
        records: list[NormalizedRecord] = []
        studies: dict[str, dict] = payload.get("studies", {})

        for nct_id, study in studies.items():
            ps = study.get("protocolSection", {})
            id_mod = ps.get("identificationModule", {})
            status_mod = ps.get("statusModule", {})
            desc_mod = ps.get("descriptionModule", {})
            design_mod = ps.get("designModule", {})
            cond_mod = ps.get("conditionsModule", {})
            sponsor_mod = ps.get("sponsorCollaboratorsModule", {})
            results_section = study.get("resultsSection", {})

            title = _safe_str(
                id_mod.get("briefTitle") or id_mod.get("officialTitle"), 1000
            )
            phase = _safe_str(
                " / ".join(design_mod.get("phases", [])) if design_mod.get("phases") else design_mod.get("phaseList", {}).get("phase", [""])[0]
            )
            status = _safe_str(status_mod.get("overallStatus"))
            conditions = cond_mod.get("conditions", [])
            sponsor = _safe_str(
                sponsor_mod.get("leadSponsor", {}).get("name")
            )
            start_date = _safe_str(
                status_mod.get("startDateStruct", {}).get("date")
                or status_mod.get("startDate")
            )
            completion_date = _safe_str(
                status_mod.get("completionDateStruct", {}).get("date")
                or status_mod.get("completionDate")
            )
            results_posted = bool(
                results_section or status_mod.get("resultsFirstPostDateStruct")
            )
            url = f"https://clinicaltrials.gov/study/{nct_id}"

            records.append(NormalizedRecord(
                record_type="trial",
                data={
                    "nct_id": nct_id,
                    "title": title,
                    "phase": phase,
                    "status": status,
                    "conditions_json": conditions,
                    "sponsor": sponsor,
                    "start_date": start_date,
                    "completion_date": completion_date,
                    "results_posted": results_posted,
                    "url": url,
                    "raw_json": study,
                },
                evidence={
                    "source": "ctgov",
                    "evidence_type": "trial",
                    "title": title,
                    "snippet_text": f"Phase: {phase} | Status: {status}",
                    "url": url,
                    "metadata_json": {"nct_id": nct_id, "phase": phase, "status": status},
                },
            ))

            # Extract adverse events from results section
            ae_section = results_section.get("adverseEventsModule", {})
            for severity_key, seriousness in [
                ("seriousEvents", "high"),
                ("otherEvents", "low"),
            ]:
                for event in ae_section.get(severity_key, []):
                    term = _safe_str(event.get("term") or event.get("title"))
                    if not term:
                        continue
                    stats = event.get("stats", [{}])
                    freq_val = stats[0].get("numAffected") if stats else None
                    denom = stats[0].get("numAtRisk") if stats else None
                    frequency = f"{freq_val}/{denom}" if freq_val is not None and denom else None

                    records.append(NormalizedRecord(
                        record_type="adverse_event",
                        data={
                            "source": "ctgov",
                            "event_term": term,
                            "seriousness": seriousness,
                            "frequency": frequency,
                            "metadata_json": {
                                "nct_id": nct_id,
                                "raw_event": event,
                            },
                        },
                    ))

        return records

    def upsert(self, session: Session, ctx: DrugContext, records: list[NormalizedRecord]) -> None:
        drug_id = ctx.drug_id

        for rec in records:
            if rec.record_type == "trial":
                d = rec.data
                stmt = pg_insert(Trial).values(
                    nct_id=d["nct_id"],
                    drug_id=drug_id,
                    title=d.get("title"),
                    phase=d.get("phase"),
                    status=d.get("status"),
                    conditions_json=d.get("conditions_json"),
                    sponsor=d.get("sponsor"),
                    start_date=d.get("start_date"),
                    completion_date=d.get("completion_date"),
                    results_posted=d.get("results_posted"),
                    url=d.get("url"),
                    raw_json=d.get("raw_json"),
                ).on_conflict_do_update(
                    index_elements=["nct_id"],
                    set_={
                        "title": d.get("title"),
                        "phase": d.get("phase"),
                        "status": d.get("status"),
                        "conditions_json": d.get("conditions_json"),
                        "results_posted": d.get("results_posted"),
                        "raw_json": d.get("raw_json"),
                    },
                )
                session.execute(stmt)

            elif rec.record_type == "adverse_event":
                d = rec.data
                session.add(AdverseEvent(
                    id=_new_uuid(),
                    drug_id=drug_id,
                    source=d.get("source", "ctgov"),
                    event_term=d["event_term"],
                    seriousness=d.get("seriousness"),
                    frequency=d.get("frequency"),
                    metadata_json=d.get("metadata_json"),
                ))

            if rec.evidence:
                ev = rec.evidence
                session.add(Evidence(
                    id=_new_uuid(), drug_id=drug_id,
                    source=ev.get("source", "ctgov"),
                    evidence_type=ev.get("evidence_type", "trial"),
                    title=ev.get("title"),
                    snippet_text=ev.get("snippet_text"),
                    url=ev.get("url"),
                    metadata_json=ev.get("metadata_json"),
                ))
