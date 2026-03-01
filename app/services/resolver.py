"""
Drug name → DrugContext resolver.

Resolution order:
  1. Generate name variants (normalize.generate_name_variants)
  2. Load manual overrides from synonyms.yaml
  3. Query PubChem for CID + authoritative synonyms
  4. Query ChEMBL for ChEMBL ID
  5. Upsert drug / drug_identifier / drug_synonym rows
  6. Return DrugContext with all gathered information
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.cache import get_or_fetch
from app.config import get_settings
from app.models import Drug, DrugIdentifier, DrugSynonym
from app.services.base import DrugContext
from app.utils.normalize import generate_name_variants, normalize_drug_name

logger = logging.getLogger(__name__)

# Path to synonyms.yaml – resolved relative to the repo root
_SYNONYMS_FILE = Path(__file__).parent.parent.parent / "synonyms.yaml"


def _load_manual_synonyms() -> dict[str, list[str]]:
    """Load synonyms.yaml; return {} on any error."""
    try:
        if not _SYNONYMS_FILE.exists():
            return {}
        with open(_SYNONYMS_FILE) as fh:
            data = yaml.safe_load(fh) or {}
        return data.get("synonyms", {})
    except Exception as exc:
        logger.warning("synonyms_yaml_load_error err=%s", exc)
        return {}


def _pubchem_resolve(name: str, all_variants: set[str]) -> tuple[str | None, set[str]]:
    """
    Try each variant against PubChem name→CID lookup.
    Returns (cid_string | None, set_of_synonyms_from_pubchem).
    """
    settings = get_settings()
    base = settings.pubchem_base_url

    # Try variants in order (canonical first)
    variants_ordered = sorted(all_variants, key=lambda v: (v != normalize_drug_name(name), v))

    for variant in variants_ordered:
        url = f"{base}/compound/name/{requests_quote(variant)}/property/IUPACName,MolecularFormula/JSON"
        try:
            data = get_or_fetch("pubchem_resolve", url)
            if isinstance(data, dict) and "PropertyTable" in data:
                props = data["PropertyTable"]["Properties"]
                if props:
                    cid = str(props[0]["CID"])
                    # Now fetch synonyms for this CID
                    syn_url = f"{base}/compound/cid/{cid}/synonyms/JSON"
                    syn_data = get_or_fetch("pubchem_synonyms", syn_url)
                    synonyms: set[str] = set()
                    if isinstance(syn_data, dict):
                        info_list = syn_data.get("InformationList", {}).get("Information", [])
                        for info in info_list:
                            for s in info.get("Synonym", []):
                                if isinstance(s, str) and len(s) <= 512:
                                    synonyms.add(s)
                    logger.info("pubchem_resolved variant=%s cid=%s synonyms=%d", variant, cid, len(synonyms))
                    return cid, synonyms
        except Exception as exc:
            logger.debug("pubchem_resolve_miss variant=%s err=%s", variant, exc)

    return None, set()


def _chembl_resolve(all_variants: set[str]) -> str | None:
    """
    Search ChEMBL molecule by name.  Returns ChEMBL ID or None.
    """
    settings = get_settings()
    base = settings.chembl_base_url

    for variant in sorted(all_variants):
        url = f"{base}/molecule.json"
        params = {"pref_name__iexact": variant, "limit": 1}
        try:
            data = get_or_fetch("chembl_resolve", url, params)
            if isinstance(data, dict):
                mols = data.get("molecules", [])
                if mols:
                    chid = mols[0].get("molecule_chembl_id")
                    if chid:
                        logger.info("chembl_resolved variant=%s chembl_id=%s", variant, chid)
                        return chid
        except Exception as exc:
            logger.debug("chembl_resolve_miss variant=%s err=%s", variant, exc)

    # Second pass: synonym search
    for variant in sorted(all_variants):
        url = f"{base}/molecule.json"
        params = {"molecule_synonyms__synonym__iexact": variant, "limit": 1}
        try:
            data = get_or_fetch("chembl_resolve_syn", url, params)
            if isinstance(data, dict):
                mols = data.get("molecules", [])
                if mols:
                    chid = mols[0].get("molecule_chembl_id")
                    if chid:
                        logger.info("chembl_synonym_resolved variant=%s chembl_id=%s", variant, chid)
                        return chid
        except Exception as exc:
            logger.debug("chembl_resolve_syn_miss variant=%s err=%s", variant, exc)

    return None


def requests_quote(s: str) -> str:
    """URL-encode a string for path segments."""
    from urllib.parse import quote
    return quote(s, safe="")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _upsert_drug(session: Session, canonical_name: str) -> str:
    """Insert or return existing drug row. Returns drug_id (UUID string)."""
    stmt = select(Drug).where(Drug.canonical_name == canonical_name)
    drug = session.execute(stmt).scalar_one_or_none()
    if drug:
        return drug.id

    import uuid
    drug = Drug(id=str(uuid.uuid4()), canonical_name=canonical_name)
    session.add(drug)
    session.flush()
    return drug.id


def _upsert_identifier(session: Session, drug_id: str, id_type: str, value: str) -> None:
    stmt = pg_insert(DrugIdentifier).values(
        id=_new_uuid(), drug_id=drug_id, id_type=id_type, value=value
    ).on_conflict_do_nothing(constraint="uq_drug_id_type_value")
    session.execute(stmt)


def _upsert_synonyms(session: Session, drug_id: str, synonyms: set[str]) -> None:
    for syn in synonyms:
        if syn and len(syn) <= 512:
            stmt = pg_insert(DrugSynonym).values(
                id=_new_uuid(), drug_id=drug_id, synonym=syn
            ).on_conflict_do_nothing(constraint="uq_drug_synonym")
            session.execute(stmt)


def _new_uuid() -> str:
    import uuid
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve(session: Session, input_name: str) -> DrugContext:
    """
    Resolve a drug name to a DrugContext and persist canonical identity to DB.

    Idempotent: calling twice for the same name returns the same drug_id.
    """
    # 1. Generate variants
    variants = generate_name_variants(input_name)
    canonical = normalize_drug_name(input_name)

    # 2. Load manual synonyms and merge
    manual = _load_manual_synonyms()
    for key, extras in manual.items():
        if key in variants or normalize_drug_name(input_name) == key:
            for e in extras:
                variants.update(generate_name_variants(e))
            break

    # Also check if any variant matches a manual key
    for key, extras in manual.items():
        if normalize_drug_name(key) in {normalize_drug_name(v) for v in variants}:
            for e in extras:
                variants.update(generate_name_variants(e))

    all_variants = variants  # growing set

    # 3. PubChem resolution
    pubchem_cid, pubchem_synonyms = _pubchem_resolve(input_name, all_variants)
    all_variants.update(pubchem_synonyms)

    # 4. ChEMBL resolution
    chembl_id = _chembl_resolve(all_variants)

    # 5. Determine best canonical name
    # Use PubChem's preferred name if available, else normalized input
    if pubchem_cid:
        try:
            settings = get_settings()
            url = f"{settings.pubchem_base_url}/compound/cid/{pubchem_cid}/property/IUPACName/JSON"
            data = get_or_fetch("pubchem_iupac", url)
            if isinstance(data, dict):
                props = data.get("PropertyTable", {}).get("Properties", [])
                if props and props[0].get("IUPACName"):
                    canonical = props[0]["IUPACName"].lower()
        except Exception:
            pass

    # 6. Persist to DB
    drug_id = _upsert_drug(session, canonical)

    if pubchem_cid:
        _upsert_identifier(session, drug_id, "pubchem_cid", pubchem_cid)
    if chembl_id:
        _upsert_identifier(session, drug_id, "chembl_id", chembl_id)

    all_synonyms = all_variants | {input_name, canonical}
    _upsert_synonyms(session, drug_id, all_synonyms)

    session.commit()

    identifiers: dict[str, str] = {}
    if pubchem_cid:
        identifiers["pubchem_cid"] = pubchem_cid
    if chembl_id:
        identifiers["chembl_id"] = chembl_id

    ctx = DrugContext(
        drug_id=drug_id,
        canonical_name=canonical,
        input_name=input_name,
        synonyms=all_synonyms,
        identifiers=identifiers,
    )
    logger.info(
        "resolved drug=%s id=%s pubchem=%s chembl=%s synonyms=%d",
        canonical, drug_id, pubchem_cid, chembl_id, len(all_synonyms),
    )
    return ctx
