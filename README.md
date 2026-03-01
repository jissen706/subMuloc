# Magellan

**Reverse discovery** — Find diseases for drugs, not the other way around.

Magellan is a drug intelligence backend that ingests drugs and diseases from public sources, maps them into a shared mechanism space, scores drug–disease relevance with explainable breakdowns, and surfaces evidence and comparators for frontend integration.

---

## What Magellan Does

| Capability | Description |
|------------|-------------|
| **Drug ingestion** | Resolve drug names → PubChem, ChEMBL, ClinicalTrials.gov, PubMed, openFDA, ClinVar. Normalize and store structured evidence. |
| **Disease ingestion** | Resolve disease queries → genes, pathways, ClinVar, PubMed. Store compact summaries for scoring. |
| **Mechanism vectors** | Map drugs and diseases into a shared mechanism vocabulary (pathways, targets, phenotypes). Compute sparse + dense vectors. |
| **Scoring** | Rank diseases for a drug using mechanism similarity, direction consistency, safety penalties, and uncertainty penalties. Full breakdown per pair. |
| **Evidence ledger** | Build structured evidence for any drug–disease pair: mechanism overlap, pathway triggers, safety flags. Deterministic, no LLM. |
| **Comparators** | Find mechanistically similar drugs and adjacent clinical conditions for a given drug. |
| **Validation** | Score health metrics, data sufficiency, and recommendations for demo readiness. |
| **Demo golden run** | Configurable script to ingest, vectorize, score, and fetch evidence for a demo set. |

---

## Quick Start

### Prerequisites

- Docker and Docker Compose

### 1. Start services

```bash
docker compose up --build
```

Starts: PostgreSQL, Redis, API (port 8000), Celery worker.

### 2. Ingest a drug and disease

```bash
# Drug (async by default; use INGEST_MODE=sync for in-process)
curl -X POST http://localhost:8000/drug/ingest -H "Content-Type: application/json" -d '{"name": "Metformin"}'

# Disease (synchronous)
curl -X POST http://localhost:8000/disease/ingest -H "Content-Type: application/json" -d '{"query": "Type 2 diabetes"}'
```

### 3. Vectorize and score

```bash
# Vectorize (use drug_id and disease_id from ingest responses)
curl -X POST http://localhost:8000/vectorize/drug/{drug_id}
curl -X POST http://localhost:8000/vectorize/disease/{disease_id}

# Rank diseases for a drug
curl -X POST http://localhost:8000/score/drug_to_diseases \
  -H "Content-Type: application/json" \
  -d '{"drug_id": "{drug_id}", "top_k": 20}'

# Get evidence for a pair
curl http://localhost:8000/pair/{drug_id}/{disease_id}/evidence
```

### 4. Demo golden run (optional)

With the API running (`INGEST_MODE=sync` recommended):

```bash
python scripts/demo_pack.py
```

Configurable via `artifacts/demo_config.json` or `DEMO_DRUGS` / `DEMO_DISEASES` env vars. Writes `artifacts/demo_report.json`.

---

## API Overview

### Meta

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe |

### Drug ingestion & summaries

| Method | Path | Description |
|--------|------|-------------|
| POST | `/drug/ingest` | Ingest drug by name. Body: `{"name": "..."}` |
| GET | `/drug/{id}/task/{task_id}/status` | Poll async ingestion status |
| GET | `/drug/{id}/summary` | Full structured summary |
| GET | `/drug/{id}/summary_short` | Compact summary for scoring |

### Disease ingestion & summaries

| Method | Path | Description |
|--------|------|-------------|
| POST | `/disease/resolve` | Resolve query → canonical name, IDs, synonyms |
| POST | `/disease/ingest` | Ingest disease. Body: `{"query": "..."}` |
| GET | `/disease/{id}/summary` | Raw stored summary |
| GET | `/disease/{id}/summary_short` | Compact summary for scoring |

### Mechanism vectors & search

| Method | Path | Description |
|--------|------|-------------|
| POST | `/vectorize/drug/{id}` | Compute + store drug vector |
| POST | `/vectorize/disease/{id}` | Compute + store disease vector |
| POST | `/vectorize/batch/diseases` | Batch vectorize diseases |
| GET | `/vector/{entity_type}/{entity_id}` | Get stored vector |
| POST | `/search/diseases_for_drug` | Cosine similarity search. Body: `drug_id`, optional `disease_ids`, `top_k` |

### Scoring & evidence

| Method | Path | Description |
|--------|------|-------------|
| POST | `/score/drug_to_diseases` | Rank diseases for a drug. Body: `drug_id`, optional `disease_ids`, `top_k`, `weights`, `include_evidence` |
| GET | `/pair/{drug_id}/{disease_id}/evidence` | Structured evidence for a pair (recomputes if not stored) |

### Comparators & node tiers

| Method | Path | Description |
|--------|------|-------------|
| GET | `/drug/{id}/comparators?top_k=10` | Similar drugs + adjacent conditions |
| GET | `/drug/{id}/node_tiers` | Mechanism node evidence tiers |

### Validation

| Method | Path | Description |
|--------|------|-------------|
| GET | `/validation/score_health` | Global metrics, per-drug metrics, data sufficiency, recommendations |

---

## Architecture

```
app/
  main.py              FastAPI app, drug ingest, summaries
  config.py             Settings (env vars)
  db.py                 SQLAlchemy engine + session
  models.py             DB models (drug, disease, mechanism_vector, pair_evidence, etc.)
  cache.py              Redis-backed HTTP cache
  routes/
    disease.py          Disease resolve, ingest, summaries
    vectorize.py        Vectorization + search
    score.py            Drug-to-diseases scoring
    evidence.py         Pair evidence
    comparator.py      Similar drugs + node tiers
    validation.py       Score health
  services/
    resolver.py         Drug name → identifiers
    disease_resolver.py Disease query → canonical
    disease_ingest.py   Disease genes, pathways, ClinVar, PubMed
    mechanism_mapper.py Drug/disease → mechanism vectors
    mechanism_vocab.py  Shared vocabulary
    scoring.py          score_pair (mechanism, direction, safety, uncertainty)
    evidence_ledger.py  build_pair_evidence, store/get
    disease_direction.py Direction consistency
    validation_engine.py Score health, data sufficiency, recommendations
    comparator_engine.py Similar drugs, adjacent conditions
  tasks/
    ingest.py           Celery drug ingestion pipeline
  postprocess/
    tox_interpreter.py  Toxicity heuristics
    pathway_extractor.py Pathway keyword extraction
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+psycopg2://druguser:drugpass@localhost:5432/drugdb` | PostgreSQL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis |
| `INGEST_MODE` | `async` | `async` (Celery) or `sync` (in-process) |
| `SCORING_DEMO_MODE` | `false` | Demo scoring tweaks when `true` |
| `NCBI_API_KEY` | `` | NCBI API key for higher rate limits |
| `PUBMED_MAX_RESULTS` | `50` | Max papers per drug |
| `CTGOV_MAX_RESULTS` | `100` | Max trials per drug |
| `CACHE_TTL_SECONDS` | `604800` | HTTP cache TTL (7 days) |

---

## Sync mode (development)

```bash
INGEST_MODE=sync docker compose up api
```

Runs drug ingestion in-process without Celery. Use for local dev and `scripts/demo_pack.py`.

---

## Local development (no Docker for app)

```bash
docker compose up postgres redis -d
pip install -r requirements.txt
DATABASE_URL=postgresql+psycopg2://druguser:drugpass@localhost:5432/drugdb alembic upgrade head
INGEST_MODE=sync DATABASE_URL=... uvicorn app.main:app --reload
```

---

## Manual synonym overrides

Edit `synonyms.yaml`:

```yaml
synonyms:
  "selumetinib":
    - "AZD6244"
    - "ARRY-142886"
```

---

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/demo_pack.py` | Demo golden run: ingest, vectorize, score, evidence, comparators. Config via `artifacts/demo_config.json` or `DEMO_DRUGS`/`DEMO_DISEASES`. |
| `scripts/validate_block2.py` | Validate mechanism vectors against real DB. |
