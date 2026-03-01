"""
PubMed ingestor via NCBI E-utilities (esearch + efetch).

Fetches:
  - Paper metadata: PMID, title, abstract, journal, year, authors

Stores into:
  publication, evidence(paper)
"""
from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.cache import get_or_fetch
from app.config import get_settings
from app.models import Evidence, Publication
from app.services.base import BaseIngestor, DrugContext, NormalizedRecord
from app.utils.text import join_authors

logger = logging.getLogger(__name__)

SEARCH_SUFFIXES = (
    "pharmacokinetics OR toxicity OR mechanism OR pathway OR mutation "
    "OR biomarker OR target OR clinical trial"
)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _build_query(search_terms: list[str]) -> str:
    """Build PubMed query string."""
    if not search_terms:
        return ""
    term_clauses = " OR ".join(
        f'"{t}"[Title/Abstract]' for t in search_terms[:10]
    )
    return f"({term_clauses}) AND ({SEARCH_SUFFIXES})"


def _parse_pubmed_xml(xml_text: str) -> list[dict[str, Any]]:
    """Parse PubMed efetch XML into list of article dicts."""
    articles = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("pubmed_xml_parse_err err=%s", exc)
        return articles

    for article_node in root.findall(".//PubmedArticle"):
        try:
            medline = article_node.find("MedlineCitation")
            if medline is None:
                continue

            pmid_node = medline.find("PMID")
            pmid = pmid_node.text if pmid_node is not None else None
            if not pmid:
                continue

            article = medline.find("Article")
            if article is None:
                continue

            title_node = article.find("ArticleTitle")
            title = "".join(title_node.itertext()) if title_node is not None else None

            abstract_node = article.find("Abstract")
            abstract_parts = []
            if abstract_node is not None:
                for at in abstract_node.findall("AbstractText"):
                    label = at.get("Label")
                    text = "".join(at.itertext())
                    if label:
                        abstract_parts.append(f"{label}: {text}")
                    else:
                        abstract_parts.append(text)
            abstract = " ".join(abstract_parts) or None

            # Journal
            journal_node = article.find("Journal")
            journal_title = None
            year = None
            if journal_node is not None:
                jt = journal_node.find("Title")
                journal_title = jt.text if jt is not None else None
                pub_date = journal_node.find(".//PubDate")
                if pub_date is not None:
                    yr = pub_date.find("Year")
                    if yr is not None:
                        try:
                            year = int(yr.text)
                        except (ValueError, TypeError):
                            pass

            # Authors
            authors = []
            author_list = article.find("AuthorList")
            if author_list is not None:
                for auth in author_list.findall("Author"):
                    last = auth.find("LastName")
                    fore = auth.find("ForeName") or auth.find("Initials")
                    name = " ".join(
                        filter(None, [
                            last.text if last is not None else None,
                            fore.text if fore is not None else None,
                        ])
                    )
                    if name:
                        authors.append(name)

            articles.append({
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "year": year,
                "journal": journal_title,
                "authors": authors,
            })
        except Exception as exc:
            logger.debug("pubmed_article_parse_err err=%s", exc)

    return articles


class PubMedIngestor(BaseIngestor):
    name = "pubmed"

    def fetch(self, ctx: DrugContext) -> dict:
        settings = get_settings()
        base = settings.pubmed_base_url
        max_results = settings.pubmed_max_results

        query = _build_query(ctx.all_search_terms())
        if not query:
            return {"articles": []}

        esearch_params: dict[str, Any] = {
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
            "usehistory": "y",
        }
        if settings.ncbi_api_key:
            esearch_params["api_key"] = settings.ncbi_api_key

        try:
            search_result = get_or_fetch(f"{self.name}_esearch", f"{base}/esearch.fcgi", esearch_params)
            if not isinstance(search_result, dict):
                return {"articles": []}

            esearch_data = search_result.get("esearchresult", {})
            pmids: list[str] = esearch_data.get("idlist", [])
            web_env = esearch_data.get("webenv")
            query_key = esearch_data.get("querykey")
        except Exception as exc:
            logger.warning("pubmed_esearch_err drug=%s err=%s", ctx.canonical_name, exc)
            return {"articles": []}

        if not pmids:
            logger.info("pubmed_no_results drug=%s", ctx.canonical_name)
            return {"articles": []}

        logger.info("pubmed_found drug=%s pmids=%d", ctx.canonical_name, len(pmids))

        # Fetch article details via efetch (XML)
        efetch_params: dict[str, Any] = {
            "db": "pubmed",
            "retmode": "xml",
            "retmax": max_results,
        }
        if settings.ncbi_api_key:
            efetch_params["api_key"] = settings.ncbi_api_key

        if web_env and query_key:
            efetch_params["WebEnv"] = web_env
            efetch_params["query_key"] = query_key
        else:
            efetch_params["id"] = ",".join(pmids[:max_results])

        try:
            xml_text = get_or_fetch(
                f"{self.name}_efetch",
                f"{base}/efetch.fcgi",
                efetch_params,
                raw_text=True,
            )
            articles = _parse_pubmed_xml(xml_text)
        except Exception as exc:
            logger.warning("pubmed_efetch_err drug=%s err=%s", ctx.canonical_name, exc)
            articles = []

        return {"articles": articles}

    def parse(self, ctx: DrugContext, payload: dict) -> list[NormalizedRecord]:
        records: list[NormalizedRecord] = []
        for article in payload.get("articles", []):
            pmid = article.get("pmid")
            if not pmid:
                continue
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            records.append(NormalizedRecord(
                record_type="publication",
                data={
                    "pmid": pmid,
                    "title": article.get("title"),
                    "abstract": article.get("abstract"),
                    "year": article.get("year"),
                    "journal": article.get("journal"),
                    "authors_json": article.get("authors", []),
                    "url": url,
                    "raw_json": article,
                },
                evidence={
                    "source": "pubmed",
                    "evidence_type": "paper",
                    "title": article.get("title"),
                    "snippet_text": (article.get("abstract") or "")[:512],
                    "url": url,
                    "metadata_json": {
                        "pmid": pmid,
                        "year": article.get("year"),
                        "journal": article.get("journal"),
                    },
                },
            ))
        return records

    def upsert(self, session: Session, ctx: DrugContext, records: list[NormalizedRecord]) -> None:
        drug_id = ctx.drug_id

        for rec in records:
            if rec.record_type == "publication":
                d = rec.data
                stmt = pg_insert(Publication).values(
                    pmid=d["pmid"],
                    drug_id=drug_id,
                    title=d.get("title"),
                    abstract=d.get("abstract"),
                    year=d.get("year"),
                    journal=d.get("journal"),
                    authors_json=d.get("authors_json"),
                    url=d.get("url"),
                    raw_json=d.get("raw_json"),
                ).on_conflict_do_update(
                    index_elements=["pmid"],
                    set_={
                        "title": d.get("title"),
                        "abstract": d.get("abstract"),
                        "year": d.get("year"),
                        "journal": d.get("journal"),
                        "authors_json": d.get("authors_json"),
                    },
                )
                session.execute(stmt)

            if rec.evidence:
                ev = rec.evidence
                session.add(Evidence(
                    id=_new_uuid(), drug_id=drug_id,
                    source=ev.get("source", "pubmed"),
                    evidence_type=ev.get("evidence_type", "paper"),
                    title=ev.get("title"),
                    snippet_text=ev.get("snippet_text"),
                    url=ev.get("url"),
                    metadata_json=ev.get("metadata_json"),
                ))
