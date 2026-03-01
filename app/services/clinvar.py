"""
ClinVar ingestor via NCBI E-utilities.

For each gene_symbol found in the `target` table for this drug,
query ClinVar and store gene/variant associations.

Stores into:
  clinvar_association, evidence(variant)
"""
from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache import get_or_fetch
from app.config import get_settings
from app.models import ClinVarAssociation, Evidence, Target
from app.services.base import BaseIngestor, DrugContext, NormalizedRecord

logger = logging.getLogger(__name__)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _parse_clinvar_xml(xml_text: str) -> list[dict[str, Any]]:
    """Parse ClinVar efetch XML (ClinVarSet format)."""
    results = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("clinvar_xml_parse_err err=%s", exc)
        return results

    for cvs in root.findall(".//ClinVarSet"):
        try:
            ref_cvs = cvs.find("ReferenceClinVarAssertion")
            if ref_cvs is None:
                continue

            # Clinical significance
            interp = ref_cvs.find(".//ClinicalSignificance/Description")
            clinical_sig = interp.text if interp is not None else None

            # Condition / trait name
            trait_set = ref_cvs.find(".//TraitSet")
            condition = None
            if trait_set is not None:
                for trait in trait_set.findall("Trait"):
                    name_node = trait.find("Name/ElementValue[@Type='Preferred']")
                    if name_node is not None:
                        condition = name_node.text
                        break

            # Variant description
            measure_set = ref_cvs.find(".//MeasureSet")
            variant = None
            gene_symbol = None
            if measure_set is not None:
                measure = measure_set.find("Measure")
                if measure is not None:
                    attr = measure.find("AttributeSet/Attribute[@Type='HGVS, coding, RefSeq']")
                    if attr is None:
                        attr = measure.find("Name/ElementValue[@Type='Preferred']")
                    variant = attr.text if attr is not None else None

                    # Gene from measure
                    gene_node = measure.find(".//Symbol/ElementValue[@Type='Preferred']")
                    if gene_node is not None:
                        gene_symbol = gene_node.text

            # ClinVar accession for URL
            accession_node = ref_cvs.find("ClinVarAccession")
            acc = accession_node.get("Acc") if accession_node is not None else None
            url = f"https://www.ncbi.nlm.nih.gov/clinvar/{acc}/" if acc else None

            results.append({
                "gene_symbol": gene_symbol,
                "variant": variant,
                "clinical_significance": clinical_sig,
                "condition": condition,
                "url": url,
                "raw": {"accession": acc},
            })
        except Exception as exc:
            logger.debug("clinvar_entry_parse_err err=%s", exc)

    return results


class ClinVarIngestor(BaseIngestor):
    name = "clinvar"

    def fetch(self, ctx: DrugContext) -> dict:
        """
        This ingestor reads gene symbols from the target table (already upserted
        by ChEMBL ingestor) and queries ClinVar for each.
        """
        # Note: fetch() is called AFTER ChEMBL upsert in the pipeline.
        # Gene symbols are stored on ctx via the pipeline's session queries.
        # We return gene_data keyed by gene symbol.
        return {"gene_symbols": list(ctx.identifiers.get("_gene_symbols_", "").split(",") if ctx.identifiers.get("_gene_symbols_") else [])}

    def fetch_with_session(self, ctx: DrugContext, session: Session) -> dict:
        """Extended fetch that queries DB for gene symbols."""
        settings = get_settings()
        base = settings.clinvar_base_url

        # Pull gene symbols from target table
        stmt = select(Target.gene_symbol).where(
            Target.drug_id == ctx.drug_id,
            Target.gene_symbol.isnot(None),
        ).distinct()
        gene_symbols: list[str] = [row[0] for row in session.execute(stmt).all() if row[0]]

        if not gene_symbols:
            logger.info("clinvar_no_genes drug=%s", ctx.canonical_name)
            return {"gene_data": {}}

        gene_data: dict[str, list[dict]] = {}

        for gene in gene_symbols[:10]:  # cap at 10 genes
            try:
                # esearch
                esearch_params: dict[str, Any] = {
                    "db": "clinvar",
                    "term": f"{gene}[Gene Name] AND (\"pathogenic\"[Clinical Significance] OR \"likely pathogenic\"[Clinical Significance])",
                    "retmax": 20,
                    "retmode": "json",
                }
                if settings.ncbi_api_key:
                    esearch_params["api_key"] = settings.ncbi_api_key

                search_data = get_or_fetch(
                    f"{self.name}_esearch",
                    f"{base}/esearch.fcgi",
                    esearch_params,
                )
                if not isinstance(search_data, dict):
                    continue

                ids = search_data.get("esearchresult", {}).get("idlist", [])
                if not ids:
                    continue

                # efetch
                efetch_params: dict[str, Any] = {
                    "db": "clinvar",
                    "id": ",".join(ids[:20]),
                    "rettype": "clinvarset",
                    "retmode": "xml",
                }
                if settings.ncbi_api_key:
                    efetch_params["api_key"] = settings.ncbi_api_key

                xml_text = get_or_fetch(
                    f"{self.name}_efetch",
                    f"{base}/efetch.fcgi",
                    efetch_params,
                    raw_text=True,
                )
                entries = _parse_clinvar_xml(xml_text)
                # Tag with the gene we searched for if missing
                for e in entries:
                    if not e.get("gene_symbol"):
                        e["gene_symbol"] = gene
                gene_data[gene] = entries
                logger.info("clinvar_found gene=%s entries=%d", gene, len(entries))

            except Exception as exc:
                logger.warning("clinvar_gene_err gene=%s err=%s", gene, exc)

        return {"gene_data": gene_data}

    def parse(self, ctx: DrugContext, payload: dict) -> list[NormalizedRecord]:
        records: list[NormalizedRecord] = []
        gene_data: dict[str, list[dict]] = payload.get("gene_data", {})

        for gene, entries in gene_data.items():
            for entry in entries:
                records.append(NormalizedRecord(
                    record_type="clinvar",
                    data={
                        "gene_symbol": entry.get("gene_symbol") or gene,
                        "variant": entry.get("variant"),
                        "clinical_significance": entry.get("clinical_significance"),
                        "condition": entry.get("condition"),
                        "url": entry.get("url"),
                        "raw_json": entry.get("raw"),
                    },
                    evidence={
                        "source": "clinvar",
                        "evidence_type": "variant",
                        "title": f"ClinVar: {entry.get('gene_symbol') or gene} – {entry.get('clinical_significance')}",
                        "snippet_text": f"{entry.get('condition')} | {entry.get('variant')}",
                        "url": entry.get("url"),
                        "metadata_json": {
                            "gene": entry.get("gene_symbol") or gene,
                            "significance": entry.get("clinical_significance"),
                        },
                    },
                ))
        return records

    def upsert(self, session: Session, ctx: DrugContext, records: list[NormalizedRecord]) -> None:
        drug_id = ctx.drug_id

        for rec in records:
            if rec.record_type == "clinvar":
                d = rec.data
                session.add(ClinVarAssociation(
                    id=_new_uuid(),
                    drug_id=drug_id,
                    gene_symbol=d.get("gene_symbol"),
                    variant=d.get("variant"),
                    clinical_significance=d.get("clinical_significance"),
                    condition=d.get("condition"),
                    url=d.get("url"),
                    raw_json=d.get("raw_json"),
                ))

            if rec.evidence:
                ev = rec.evidence
                session.add(Evidence(
                    id=_new_uuid(), drug_id=drug_id,
                    source=ev.get("source", "clinvar"),
                    evidence_type=ev.get("evidence_type", "variant"),
                    title=ev.get("title"),
                    snippet_text=ev.get("snippet_text"),
                    url=ev.get("url"),
                    metadata_json=ev.get("metadata_json"),
                ))

    def run(self, session: Session, ctx: DrugContext) -> list[NormalizedRecord]:
        """Override run() to use fetch_with_session for DB access."""
        import logging
        logger_run = logging.getLogger(f"ingestor.{self.name}")
        try:
            payload = self.fetch_with_session(ctx, session)
            records = self.parse(ctx, payload)
            self.upsert(session, ctx, records)
            session.commit()
            logger_run.info("ingestor=%s drug=%s records=%d", self.name, ctx.canonical_name, len(records))
            return records
        except Exception as exc:
            session.rollback()
            logger_run.error("ingestor=%s drug=%s error=%s", self.name, ctx.canonical_name, exc, exc_info=True)
            return []
