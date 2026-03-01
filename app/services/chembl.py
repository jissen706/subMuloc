"""
ChEMBL ingestor.

Fetches:
  - Molecule metadata (ChEMBL ID confirmed, pref_name, synonyms)
  - Drug mechanisms / targets (target name, gene symbols, action type)

Stores into:
  target, drug_identifier, drug_synonym, evidence(target)
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.cache import get_or_fetch
from app.config import get_settings
from app.models import DrugIdentifier, DrugSynonym, Evidence, Target
from app.services.base import BaseIngestor, DrugContext, NormalizedRecord

logger = logging.getLogger(__name__)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class ChEMBLIngestor(BaseIngestor):
    name = "chembl"

    def fetch(self, ctx: DrugContext) -> dict:
        settings = get_settings()
        base = settings.chembl_base_url
        result: dict = {"molecule": {}, "mechanisms": [], "targets": {}}

        chembl_id = ctx.chembl_id()

        # If no ChEMBL ID, search by synonyms
        if not chembl_id:
            for term in ctx.all_search_terms()[:8]:
                try:
                    url = f"{base}/molecule.json"
                    data = get_or_fetch(self.name, url, {"pref_name__iexact": term, "limit": 1})
                    if isinstance(data, dict) and data.get("molecules"):
                        chembl_id = data["molecules"][0].get("molecule_chembl_id")
                        result["molecule"] = data["molecules"][0]
                        break
                except Exception as exc:
                    logger.debug("chembl_mol_miss term=%s err=%s", term, exc)

        if not chembl_id:
            # Try synonym search
            for term in ctx.all_search_terms()[:8]:
                try:
                    url = f"{base}/molecule.json"
                    data = get_or_fetch(f"{self.name}_syn", url, {
                        "molecule_synonyms__synonym__iexact": term, "limit": 1
                    })
                    if isinstance(data, dict) and data.get("molecules"):
                        chembl_id = data["molecules"][0].get("molecule_chembl_id")
                        result["molecule"] = data["molecules"][0]
                        break
                except Exception as exc:
                    logger.debug("chembl_syn_miss term=%s err=%s", term, exc)

        if not chembl_id:
            logger.info("chembl_no_id_found drug=%s", ctx.canonical_name)
            return result

        result["chembl_id"] = chembl_id

        # Fetch full molecule if not yet populated
        if not result["molecule"]:
            try:
                url = f"{base}/molecule/{chembl_id}.json"
                data = get_or_fetch(self.name, url)
                result["molecule"] = data or {}
            except Exception as exc:
                logger.warning("chembl_mol_fetch_err id=%s err=%s", chembl_id, exc)

        # Fetch mechanisms
        try:
            url = f"{base}/mechanism.json"
            data = get_or_fetch(f"{self.name}_mech", url, {
                "molecule_chembl_id": chembl_id, "limit": 100
            })
            if isinstance(data, dict):
                result["mechanisms"] = data.get("mechanisms", [])
        except Exception as exc:
            logger.warning("chembl_mech_err id=%s err=%s", chembl_id, exc)

        # Fetch target details for each mechanism's target
        target_ids: set[str] = set()
        for mech in result["mechanisms"]:
            tid = mech.get("target_chembl_id")
            if tid:
                target_ids.add(tid)

        for tid in list(target_ids)[:20]:  # cap at 20 targets
            try:
                url = f"{base}/target/{tid}.json"
                tdata = get_or_fetch(f"{self.name}_target", url)
                if isinstance(tdata, dict):
                    result["targets"][tid] = tdata
            except Exception as exc:
                logger.debug("chembl_target_err tid=%s err=%s", tid, exc)

        return result

    def parse(self, ctx: DrugContext, payload: dict) -> list[NormalizedRecord]:
        records: list[NormalizedRecord] = []
        chembl_id = payload.get("chembl_id") or ctx.chembl_id()
        molecule = payload.get("molecule", {})
        mechanisms = payload.get("mechanisms", [])
        targets_data = payload.get("targets", {})

        if not chembl_id:
            return records

        # ChEMBL identifier record
        records.append(NormalizedRecord(
            record_type="identifier",
            data={"id_type": "chembl_id", "value": chembl_id},
        ))

        # Synonyms from molecule
        for syn_entry in molecule.get("molecule_synonyms", []):
            syn = syn_entry.get("molecule_synonym") or syn_entry.get("syn_type")
            if syn and isinstance(syn, str) and len(syn) <= 512:
                records.append(NormalizedRecord(
                    record_type="synonym",
                    data={"synonym": syn},
                ))

        # Preferred name as synonym
        pref_name = molecule.get("pref_name")
        if pref_name and isinstance(pref_name, str):
            records.append(NormalizedRecord(
                record_type="synonym",
                data={"synonym": pref_name},
            ))

        # Targets from mechanisms
        for mech in mechanisms:
            tid = mech.get("target_chembl_id")
            target_info = targets_data.get(tid, {}) if tid else {}

            target_name = (
                target_info.get("pref_name")
                or mech.get("target_name")
                or tid
                or "unknown"
            )

            # Extract gene symbols from target components
            gene_symbols: list[str] = []
            for comp in target_info.get("target_components", []):
                for syn in comp.get("target_component_synonyms", []):
                    if syn.get("syn_type") == "GENE_SYMBOL":
                        gene_symbols.append(syn["component_synonym"])

            gene_symbol = gene_symbols[0] if gene_symbols else None

            evidence_data = {
                "action_type": mech.get("action_type"),
                "mechanism_of_action": mech.get("mechanism_of_action"),
                "target_chembl_id": tid,
                "molecule_chembl_id": chembl_id,
                "all_gene_symbols": gene_symbols,
            }

            records.append(NormalizedRecord(
                record_type="target",
                data={
                    "target_name": str(target_name)[:512],
                    "gene_symbol": str(gene_symbol)[:128] if gene_symbol else None,
                    "source": "chembl",
                    "evidence": evidence_data,
                },
                evidence={
                    "source": "chembl",
                    "evidence_type": "target",
                    "title": f"{ctx.canonical_name} → {target_name}",
                    "snippet_text": mech.get("mechanism_of_action", ""),
                    "url": f"https://www.ebi.ac.uk/chembl/compound_report_card/{chembl_id}/",
                    "metadata_json": evidence_data,
                },
            ))

        return records

    def upsert(self, session: Session, ctx: DrugContext, records: list[NormalizedRecord]) -> None:
        drug_id = ctx.drug_id

        for rec in records:
            if rec.record_type == "identifier":
                stmt = pg_insert(DrugIdentifier).values(
                    id=_new_uuid(), drug_id=drug_id,
                    id_type=rec.data["id_type"], value=rec.data["value"],
                ).on_conflict_do_nothing(constraint="uq_drug_id_type_value")
                session.execute(stmt)
                ctx.identifiers[rec.data["id_type"]] = rec.data["value"]

            elif rec.record_type == "synonym":
                syn = rec.data["synonym"]
                if syn:
                    stmt = pg_insert(DrugSynonym).values(
                        id=_new_uuid(), drug_id=drug_id, synonym=syn,
                    ).on_conflict_do_nothing(constraint="uq_drug_synonym")
                    session.execute(stmt)
                    ctx.synonyms.add(syn)

            elif rec.record_type == "target":
                d = rec.data
                stmt = pg_insert(Target).values(
                    id=_new_uuid(), drug_id=drug_id,
                    target_name=d.get("target_name"),
                    gene_symbol=d.get("gene_symbol"),
                    source=d.get("source", "chembl"),
                    evidence=d.get("evidence"),
                ).on_conflict_do_nothing(constraint="uq_target")
                session.execute(stmt)

            if rec.evidence:
                ev = rec.evidence
                session.add(Evidence(
                    id=_new_uuid(), drug_id=drug_id,
                    source=ev.get("source", "chembl"),
                    evidence_type=ev.get("evidence_type", "target"),
                    title=ev.get("title"),
                    snippet_text=ev.get("snippet_text"),
                    url=ev.get("url"),
                    metadata_json=ev.get("metadata_json"),
                ))
