# Bootstrap / Quickstart Seed

Bootstrap endpoints provide a curated quickstart dataset for local development. **Disabled by default.**

## Enable

```bash
ENABLE_BOOTSTRAP_ROUTES=true uvicorn app.main:app --reload
```

## Config

Create `artifacts/bootstrap_seed.json` locally (copy from `artifacts/bootstrap_seed.example.json`). Do not commit the real file.

Override via env:
- `BOOTSTRAP_SEED_PATH` — path to config file
- `BOOTSTRAP_DRUGS` — comma-separated drug names
- `BOOTSTRAP_DISEASES` — comma-separated disease queries

## Endpoints

### POST /bootstrap/seed

Seed drugs and diseases from config. Idempotent.

```bash
curl -X POST http://localhost:8000/bootstrap/seed
```

Optional body:
```json
{"force": false, "ingest_mode": "sync", "poll_timeout_s": 180}
```

### POST /bootstrap/run

Ensure seed, resolve drug, score against diseases, return UI-ready response.

```bash
curl -X POST http://localhost:8000/bootstrap/run \
  -H "Content-Type: application/json" \
  -d '{"drug_name":"Ruxolitinib","top_k":10}'
```

Optional: `restrict_to_bootstrap_diseases: false` to score against all vectorized diseases.

## Verify matching quality

To verify drug→disease matching produces signal:

```bash
ENABLE_BOOTSTRAP_ROUTES=true uvicorn app.main:app &
python scripts/bootstrap_acceptance.py
```

Optional: `SCORING_DEMO_MODE=true` for relaxed score thresholds.

Override anchor drugs via `BOOTSTRAP_ACCEPT_DRUGS` (comma-separated).

## Production

Keep bootstrap endpoints **disabled** in production. Do not set `ENABLE_BOOTSTRAP_ROUTES` in deployed environments.
