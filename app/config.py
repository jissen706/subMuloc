from __future__ import annotations

import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://druguser:drugpass@localhost:5432/drugdb",
    )

    # Redis / Celery
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    celery_broker_url: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    celery_result_backend: str = os.getenv(
        "CELERY_RESULT_BACKEND", "redis://localhost:6379/1"
    )

    # Ingestion mode: "async" (Celery) | "sync"
    ingest_mode: str = os.getenv("INGEST_MODE", "async")

    # Cache TTL in seconds (default 7 days)
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", str(7 * 24 * 3600)))

    # External API base URLs
    pubchem_base_url: str = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
    chembl_base_url: str = "https://www.ebi.ac.uk/chembl/api/data"
    ctgov_base_url: str = "https://clinicaltrials.gov/api/v2"
    pubmed_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    openfda_base_url: str = "https://api.fda.gov/drug"
    clinvar_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    clinvar_esearch_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    # NCBI API key (optional but recommended for rate limits)
    ncbi_api_key: str = os.getenv("NCBI_API_KEY", "")

    # PubMed search limits
    pubmed_max_results: int = int(os.getenv("PUBMED_MAX_RESULTS", "50"))
    ctgov_max_results: int = int(os.getenv("CTGOV_MAX_RESULTS", "100"))

    # HTTP timeouts
    http_timeout: int = int(os.getenv("HTTP_TIMEOUT", "30"))
    http_max_retries: int = int(os.getenv("HTTP_MAX_RETRIES", "3"))

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
