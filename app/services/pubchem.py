"""
PubChem ingestor.

Fetches:
  - Canonical SMILES, InChI, molecular formula, molecular weight
  - Full synonyms list
  - Additional identifiers (CAS, InChIKey)

Stores into:
  molecular_structure, drug_identifier, drug_synonym, evidence(structure)
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.cache import get_or_fetch
from app.config import get_settings
from app.models import DrugIdentifier, DrugSynonym, Evidence, MolecularStructure
from app.services.base import BaseIngestor, DrugContext, NormalizedRecord

logger = logging.getLogger(__name__)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class PubChemIngestor(BaseIngestor):
    name = "pubchem"

    def fetch(self, ctx: DrugContext) -> dict:
        """Fetch compound properties + synonyms from PubChem."""
        settings = get_settings()
        base = settings.pubchem_base_url
        result: dict = {}

        cid = ctx.pubchem_cid()

        # If we don't have a CID, try to look it up by name
        if not cid:
            for term in ctx.all_search_terms()[:5]:
                try:
                    from urllib.parse import quote
                    url = f"{base}/compound/name/{quote(term, safe='')}/property/CanonicalSMILES,IsomericSMILES,InChI,InChIKey,MolecularFormula,MolecularWeight/JSON"
                    data = get_or_fetch(self.name, url)
                    if isinstance(data, dict) and "PropertyTable" in data:
                        props = data["PropertyTable"]["Properties"]
                        if props:
                            cid = str(props[0]["CID"])
                            result["properties"] = props[0]
                            break
                except Exception as exc:
                    logger.debug("pubchem_prop_miss term=%s err=%s", term, exc)

        if cid and "properties" not in result:
            try:
                url = f"{base}/compound/cid/{cid}/property/CanonicalSMILES,IsomericSMILES,InChI,InChIKey,MolecularFormula,MolecularWeight/JSON"
                data = get_or_fetch(self.name, url)
                if isinstance(data, dict) and "PropertyTable" in data:
                    props = data["PropertyTable"]["Properties"]
                    if props:
                        result["properties"] = props[0]
            except Exception as exc:
                logger.warning("pubchem_prop_fetch_err cid=%s err=%s", cid, exc)

        if cid:
            result["cid"] = cid
            try:
                syn_url = f"{base}/compound/cid/{cid}/synonyms/JSON"
                syn_data = get_or_fetch(f"{self.name}_synonyms", syn_url)
                synonyms = []
                if isinstance(syn_data, dict):
                    info_list = syn_data.get("InformationList", {}).get("Information", [])
                    for info in info_list:
                        synonyms.extend(info.get("Synonym", []))
                result["synonyms"] = [s for s in synonyms if isinstance(s, str) and len(s) <= 512]
            except Exception as exc:
                logger.warning("pubchem_syn_err cid=%s err=%s", cid, exc)
                result["synonyms"] = []

        return result

    def parse(self, ctx: DrugContext, payload: dict) -> list[NormalizedRecord]:
        records: list[NormalizedRecord] = []
        props = payload.get("properties", {})
        cid = payload.get("cid") or ctx.pubchem_cid()
        synonyms: list[str] = payload.get("synonyms", [])

        if not props and not cid:
            return records

        # Structure record
        records.append(NormalizedRecord(
            record_type="structure",
            data={
                "smiles": props.get("IsomericSMILES") or props.get("CanonicalSMILES"),
                "inchi": props.get("InChI"),
                "molecular_formula": props.get("MolecularFormula"),
                "molecular_weight": props.get("MolecularWeight"),
            },
            evidence={
                "source": "pubchem",
                "evidence_type": "structure",
                "title": f"Molecular structure for {ctx.canonical_name}",
                "snippet_text": f"PubChem CID: {cid}",
                "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}" if cid else None,
                "metadata_json": {"cid": cid, "properties": props},
            },
        ))

        # Identifier records
        if cid:
            records.append(NormalizedRecord(
                record_type="identifier",
                data={"id_type": "pubchem_cid", "value": str(cid)},
            ))
        if props.get("InChIKey"):
            records.append(NormalizedRecord(
                record_type="identifier",
                data={"id_type": "inchikey", "value": props["InChIKey"]},
            ))
        if props.get("InChI"):
            records.append(NormalizedRecord(
                record_type="identifier",
                data={"id_type": "inchi", "value": props["InChI"][:2048]},
            ))
        smiles = props.get("IsomericSMILES") or props.get("CanonicalSMILES")
        if smiles:
            records.append(NormalizedRecord(
                record_type="identifier",
                data={"id_type": "smiles", "value": smiles[:2048]},
            ))

        # Parse CAS from synonyms
        import re
        cas_pattern = re.compile(r"^\d{2,7}-\d{2}-\d$")
        for syn in synonyms:
            if cas_pattern.match(syn.strip()):
                records.append(NormalizedRecord(
                    record_type="identifier",
                    data={"id_type": "cas", "value": syn.strip()},
                ))
                break  # only first CAS

        # Synonym records (limit to first 200)
        for syn in synonyms[:200]:
            records.append(NormalizedRecord(
                record_type="synonym",
                data={"synonym": syn},
            ))

        return records

    def upsert(self, session: Session, ctx: DrugContext, records: list[NormalizedRecord]) -> None:
        drug_id = ctx.drug_id

        for rec in records:
            if rec.record_type == "structure":
                d = rec.data
                existing = session.get(MolecularStructure, drug_id)
                if existing:
                    if d.get("smiles"):
                        existing.smiles = d["smiles"]
                    if d.get("inchi"):
                        existing.inchi = d["inchi"]
                    if d.get("molecular_formula"):
                        existing.molecular_formula = d["molecular_formula"]
                    if d.get("molecular_weight") is not None:
                        existing.molecular_weight = float(d["molecular_weight"])
                else:
                    ms = MolecularStructure(
                        drug_id=drug_id,
                        smiles=d.get("smiles"),
                        inchi=d.get("inchi"),
                        molecular_formula=d.get("molecular_formula"),
                        molecular_weight=float(d["molecular_weight"]) if d.get("molecular_weight") else None,
                    )
                    session.add(ms)

            elif rec.record_type == "identifier":
                stmt = pg_insert(DrugIdentifier).values(
                    id=_new_uuid(),
                    drug_id=drug_id,
                    id_type=rec.data["id_type"],
                    value=rec.data["value"],
                ).on_conflict_do_nothing(constraint="uq_drug_id_type_value")
                session.execute(stmt)
                # Update ctx for downstream ingestors
                ctx.identifiers[rec.data["id_type"]] = rec.data["value"]

            elif rec.record_type == "synonym":
                syn = rec.data["synonym"]
                if syn and len(syn) <= 512:
                    stmt = pg_insert(DrugSynonym).values(
                        id=_new_uuid(),
                        drug_id=drug_id,
                        synonym=syn,
                    ).on_conflict_do_nothing(constraint="uq_drug_synonym")
                    session.execute(stmt)
                    ctx.synonyms.add(syn)

            # Evidence
            if rec.evidence:
                ev = rec.evidence
                session.add(Evidence(
                    id=_new_uuid(),
                    drug_id=drug_id,
                    source=ev.get("source", "pubchem"),
                    evidence_type=ev.get("evidence_type", "structure"),
                    title=ev.get("title"),
                    snippet_text=ev.get("snippet_text"),
                    url=ev.get("url"),
                    metadata_json=ev.get("metadata_json"),
                ))
