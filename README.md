# Drug Intelligence Ingestion Platform

Backend-only evidence ingestion and normalization system for drug intelligence.

## What it does

Given a drug name or code, this platform:
1. Resolves canonical identifiers and synonyms (PubChem + ChEMBL + manual overrides)
2. Pulls structured data from 6 public sources: ClinicalTrials.gov, PubMed, ChEMBL, PubChem, openFDA, ClinVar
3. Normalizes and stores everything into PostgreSQL
4. Runs lightweight post-processing for toxicity flags and pathway keyword extraction
5. Returns a structured JSON summary via REST API

**No frontend. No disease matching. No ranking or recommendation logic.**

---

## Quick Start

### Prerequisites

- Docker and Docker Compose

### 1. Start all services

```bash
docker compose up --build
```

This starts:
- `postgres` — PostgreSQL 16
- `redis` — Redis 7
- `migrate` — runs Alembic migrations and exits
- `api` — FastAPI on port 8000
- `worker` — Celery worker (2 concurrent tasks)

### 2. Wait for services to be healthy (~15-30 seconds)

```bash
docker compose ps
```

All services should show `healthy` or `running`.

### 3. Ingest a drug

```bash
# Async mode (default) — returns immediately, worker processes in background
curl -X POST http://localhost:8000/drug/ingest \
  -H "Content-Type: application/json" \
  -d '{"name": "rapamycin"}'
```

Response:
```json
{
  "drug_id": "uuid-here",
  "canonical_name": "rapamycin",
  "status": "queued",
  "task_id": "celery-task-id",
  "message": "Ingestion queued. Poll GET /drug/{id}/summary for results."
}
```

### 4. Query the summary (after worker completes ~1-5 min)

```bash
curl http://localhost:8000/drug/{drug_id}/summary
```

---

## Synchronous mode (for development/testing)

Set `INGEST_MODE=sync` to run the full pipeline in-process without Celery:

```bash
INGEST_MODE=sync docker compose up api
```

Or set it in your local `.env` file.

---

## API Reference

### `POST /drug/ingest`

**Body:** `{ "name": "selumetinib" }`

**Returns:**
```json
{
  "drug_id": "...",
  "canonical_name": "...",
  "status": "queued | completed",
  "task_id": "...",
  "counts": {
    "trials": 42,
    "publications": 50,
    "targets": 3,
    "label_warnings": 5,
    "adverse_events": 20,
    "clinvar_associations": 8,
    "toxicity_metrics": 4,
    "pathway_mentions": 150
  }
}
```

### `GET /drug/{id}/summary`

Returns full structured JSON with:
- Canonical name, identifiers, synonyms
- Molecular structure (SMILES, InChI, formula, weight)
- Targets (from ChEMBL)
- Trials grouped by phase/status
- Publications by year
- FDA label warnings
- Toxicity metrics with interpreted flags
- Pathway keyword mentions (aggregated by term)
- ClinVar variant associations

### `GET /drug/{id}/task/{task_id}/status`

Poll Celery task status for async ingestion.

### `GET /health`

Liveness probe.

---

## Disease (Block 1)

Disease ingestion and short summary (first-class entities alongside drugs).

### CURL demo

```bash
# 1) Ingest a disease (synchronous)
curl -X POST http://localhost:8000/disease/ingest \
  -H "Content-Type: application/json" \
  -d '{"query": "Brugada syndrome"}'

# 2) Get short summary (use disease_id from step 1)
curl http://localhost:8000/disease/{disease_id}/summary_short
```

### Endpoints

- **POST /disease/resolve** — Resolve query to canonical_name, ids (orpha/omim), synonyms, resolver_notes.
- **POST /disease/ingest** — Resolve, upsert disease, run ingestion, store raw artifact; returns disease_id.
- **GET /disease/{disease_id}/summary** — Raw stored JSON.
- **GET /disease/{disease_id}/summary_short** — Deterministic compact JSON (version `disease_short_v1`).

### Smoke test list (manual ingestion)

- Brugada syndrome
- Catecholaminergic polymorphic ventricular tachycardia
- Short QT syndrome
- Hypertrophic cardiomyopathy
- Arrhythmogenic right ventricular cardiomyopathy
- Loeys-Dietz syndrome
- Milroy disease
- Lymphedema-distichiasis syndrome
- Bardet-Biedl syndrome
- Joubert syndrome

---

## Architecture

```
app/
  main.py              FastAPI application + endpoints
  config.py            Settings (env vars)
  db.py                SQLAlchemy engine + session
  models.py            All DB models (12 tables)
  cache.py             Redis-backed HTTP cache (7-day TTL)
  schemas/
    drug.py            Ingest request/response schemas
    summary.py         Summary response schema
  services/
    base.py            DrugContext, NormalizedRecord, SourceIngestor protocol
    resolver.py        Name → identifiers (PubChem + ChEMBL)
    pubchem.py         Molecular structure + identifiers
    chembl.py          Drug targets + mechanisms
    ctgov.py           Clinical trials
    pubmed.py          Publications (E-utilities)
    openfda.py         FDA label warnings + FAERS AE signals
    clinvar.py         Gene/variant associations
  postprocess/
    tox_interpreter.py Toxicity heuristics (DB read-only)
    pathway_extractor.py Pathway keyword extraction (DB read-only)
  tasks/
    ingest.py          Celery task + INGESTORS registry
  utils/
    normalize.py       Drug name normalization + variant generation
    text.py            Snippet extraction + keyword scanning
  migrations/          Alembic migrations
```

### Adding a new data source

1. Create `app/services/mysource.py` implementing `BaseIngestor`
2. Add one line to `INGESTORS` list in `app/tasks/ingest.py`

That's it — the pipeline runner handles the rest.

---

## Configuration

All settings via environment variables (or `.env` file):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://druguser:drugpass@localhost:5432/drugdb` | PostgreSQL connection |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `INGEST_MODE` | `async` | `async` (Celery) or `sync` (in-process) |
| `NCBI_API_KEY` | `` | NCBI API key for higher PubMed/ClinVar rate limits |
| `PUBMED_MAX_RESULTS` | `50` | Max papers to fetch per drug |
| `CTGOV_MAX_RESULTS` | `100` | Max trials to fetch per drug |
| `CACHE_TTL_SECONDS` | `604800` | HTTP cache TTL (7 days) |

---

## Manual synonym overrides

Edit `synonyms.yaml` to add known aliases:

```yaml
synonyms:
  "selumetinib":
    - "AZD6244"
    - "ARRY-142886"
```

---

## Database

PostgreSQL with 12 tables:

`drug` → `drug_identifier` → `drug_synonym` → `molecular_structure` → `target` → `trial` → `publication` → `label_warning` → `adverse_event` → `clinvar_association` → `toxicity_metric` → `disease_pathway_mention` + `evidence` (generic)

All ingestors write to both structured tables and the generic `evidence` table for traceability.

---

## Local development (without Docker)

```bash
# Start only the infrastructure
docker compose up postgres redis -d

# Install dependencies
pip install -r requirements.txt

# Run migrations
DATABASE_URL=postgresql+psycopg2://druguser:drugpass@localhost:5432/drugdb alembic upgrade head

# Start API in sync mode
INGEST_MODE=sync DATABASE_URL=postgresql+psycopg2://druguser:drugpass@localhost:5432/drugdb \
  uvicorn app.main:app --reload

# Start Celery worker (separate terminal)
DATABASE_URL=postgresql+psycopg2://druguser:drugpass@localhost:5432/drugdb \
  celery -A app.tasks.ingest.celery_app worker --loglevel=info
```
