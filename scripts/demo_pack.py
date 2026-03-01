#!/usr/bin/env python3
"""
Demo readiness golden run: ingest demo drugs/diseases, vectorize, score, fetch evidence.

Configurable without code edits via:
  - artifacts/demo_config.json (or path in DEMO_CONFIG_PATH)
  - DEMO_DRUGS (comma-separated) and DEMO_DISEASES (comma-separated) env vars

Usage examples:
  # Default run (Metformin, Imatinib, Dasatinib + 10 diseases)
  python scripts/demo_pack.py

  # Override via env vars
  DEMO_DRUGS="Rapamycin,Ruxolitinib" DEMO_DISEASES="Brugada syndrome,STING-associated vasculopathy" python scripts/demo_pack.py

  # Override via config file (create artifacts/demo_config.json first)
  python scripts/demo_pack.py

  # Custom config path
  DEMO_CONFIG_PATH="artifacts/demo_config.json" python scripts/demo_pack.py

Run with API up: INGEST_MODE=sync uvicorn app.main:app
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Hardcoded defaults (fallback when no config/env)
DEFAULT_DRUGS = ["Metformin", "Imatinib", "Dasatinib"]
DEFAULT_DISEASES = [
    "Type 2 diabetes",
    "Chronic myeloid leukemia",
    "Brugada syndrome",
    "Hypertension",
    "Rheumatoid arthritis",
    "Asthma",
    "Alzheimer disease",
    "Parkinson disease",
    "Multiple sclerosis",
    "Crohn disease",
]
DEFAULT_TOP_K = 20
DEFAULT_SKIP_EXISTING = True
DEFAULT_COMPARATORS_TOP_K = 10
DEFAULT_REQUIRE_MIN_DRUGS = 3
DEFAULT_REQUIRE_MIN_DISEASES = 8


def _parse_env_list(val: str | None) -> list[str]:
    """Parse comma-separated env var: strip, drop empty, de-duplicate preserving order."""
    if not val or not isinstance(val, str):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for part in val.split(","):
        s = part.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _load_config() -> tuple[list[str], list[str], int, bool, int, int, int, str]:
    """
    Load effective config. Priority: DEMO_DRUGS/DEMO_DISEASES env > config file > defaults.
    Returns: (drugs, diseases, top_k, skip_existing, comparators_top_k, require_min_drugs, require_min_diseases, config_source)
    """
    config_source = "defaults"
    drugs: list[str] = list(DEFAULT_DRUGS)
    diseases: list[str] = list(DEFAULT_DISEASES)
    top_k = DEFAULT_TOP_K
    skip_existing = DEFAULT_SKIP_EXISTING
    comparators_top_k = DEFAULT_COMPARATORS_TOP_K
    require_min_drugs = DEFAULT_REQUIRE_MIN_DRUGS
    require_min_diseases = DEFAULT_REQUIRE_MIN_DISEASES

    # Try config file (unless env vars override drugs/diseases)
    config_path = os.environ.get("DEMO_CONFIG_PATH")
    if config_path is None:
        config_path = os.path.join(ROOT, "artifacts", "demo_config.json")

    file_data: dict | None = None
    if os.path.isfile(config_path):
        try:
            with open(config_path) as f:
                file_data = json.load(f)
            if file_data is not None and isinstance(file_data, dict):
                config_source = "file"
        except json.JSONDecodeError as e:
            print(f"Warning: malformed JSON in {config_path}: {e}. Using defaults.", file=sys.stderr)
        except OSError as e:
            print(f"Warning: could not read {config_path}: {e}. Using defaults.", file=sys.stderr)

    # Apply file config (missing keys keep current values)
    if file_data:
        if isinstance(file_data.get("drugs"), list):
            drugs = [str(x).strip() for x in file_data["drugs"] if str(x).strip()]
        if isinstance(file_data.get("diseases"), list):
            diseases = [str(x).strip() for x in file_data["diseases"] if str(x).strip()]
        if isinstance(file_data.get("top_k"), (int, float)):
            top_k = max(1, min(200, int(file_data["top_k"])))
        if "skip_existing" in file_data:
            skip_existing = bool(file_data["skip_existing"])
        if isinstance(file_data.get("comparators_top_k"), (int, float)):
            comparators_top_k = max(1, min(200, int(file_data["comparators_top_k"])))
        if isinstance(file_data.get("require_min_drugs"), (int, float)):
            require_min_drugs = max(0, int(file_data["require_min_drugs"]))
        if isinstance(file_data.get("require_min_diseases"), (int, float)):
            require_min_diseases = max(0, int(file_data["require_min_diseases"]))

    # Env vars override (highest priority)
    env_drugs = _parse_env_list(os.environ.get("DEMO_DRUGS"))
    env_diseases = _parse_env_list(os.environ.get("DEMO_DISEASES"))
    if env_drugs:
        drugs = env_drugs
        config_source = "env"
    if env_diseases:
        diseases = env_diseases
        config_source = "env"

    # Cap top_k to len(diseases) when scoring against restricted disease list
    if diseases:
        top_k = min(top_k, len(diseases))

    return drugs, diseases, top_k, skip_existing, comparators_top_k, require_min_drugs, require_min_diseases, config_source


def _req(method: str, path: str, json_body: dict | None = None) -> dict:
    import requests
    base_url = os.environ.get("DEMO_BASE_URL", "http://localhost:8000")
    url = f"{base_url.rstrip('/')}{path}"
    r = requests.request(method, url, json=json_body, timeout=120)
    r.raise_for_status()
    return r.json() if r.content else {}


def main() -> int:
    drugs, diseases, top_k, skip_existing, comparators_top_k, require_min_drugs, require_min_diseases, config_source = _load_config()

    os.makedirs(os.path.join(ROOT, "artifacts"), exist_ok=True)
    report: dict = {
        "effective_config": {
            "drugs": drugs,
            "diseases": diseases,
            "top_k": top_k,
            "skip_existing": skip_existing,
            "comparators_top_k": comparators_top_k,
            "require_min_drugs": require_min_drugs,
            "require_min_diseases": require_min_diseases,
        },
        "config_source": config_source,
        "successes": [],
        "failures": [],
        "drugs_ingested": [],
        "diseases_ingested": [],
        "top_results": {},
        "evidence_presence": {},
        "comparator_condition_counts": {},
    }

    drug_ids: list[str] = []
    disease_ids: list[str] = []

    # 1) Ingest drugs
    for name in drugs:
        try:
            out = _req("POST", "/drug/ingest", {"name": name})
            drug_id = out.get("drug_id") or out.get("canonical_name", "")
            status = out.get("status", "")
            if status == "queued":
                task_id = out.get("task_id")
                for _ in range(60):
                    st = _req("GET", f"/drug/{drug_id}/task/{task_id}/status")
                    if st.get("state") == "SUCCESS":
                        drug_ids.append(drug_id)
                        report["drugs_ingested"].append({"drug_id": drug_id, "name": name})
                        break
                    if st.get("state") == "FAILURE":
                        report["failures"].append({"step": "ingest_drug", "name": name, "error": st.get("result")})
                        break
                    time.sleep(2)
                else:
                    report["failures"].append({"step": "ingest_drug", "name": name, "error": "timeout"})
            else:
                drug_ids.append(drug_id)
                report["drugs_ingested"].append({"drug_id": drug_id, "name": name})
        except Exception as e:
            report["failures"].append({"step": "ingest_drug", "name": name, "error": str(e)})

    # 2) Ingest diseases
    for query in diseases:
        try:
            out = _req("POST", "/disease/ingest", {"query": query})
            disease_id = out.get("disease_id", "")
            disease_ids.append(disease_id)
            report["diseases_ingested"].append({"disease_id": disease_id, "query": query})
        except Exception as e:
            report["failures"].append({"step": "ingest_disease", "query": query, "error": str(e)})

    # 3) Vectorize drugs
    for did in drug_ids:
        try:
            _req("POST", f"/vectorize/drug/{did}")
        except Exception as e:
            report["failures"].append({"step": "vectorize_drug", "drug_id": did, "error": str(e)})

    # 4) Vectorize diseases (batch)
    if disease_ids:
        try:
            _req("POST", "/vectorize/batch/diseases", {"disease_ids": disease_ids, "skip_existing": skip_existing})
        except Exception as e:
            report["failures"].append({"step": "vectorize_diseases", "error": str(e)})

    drugs_vectorized = len(drug_ids)
    diseases_vectorized = len(disease_ids)

    if drugs_vectorized < require_min_drugs:
        report["failures"].append({"step": "check", "error": f"Fewer than {require_min_drugs} drugs ingested+vectorized: {drugs_vectorized}"})
    if diseases_vectorized < require_min_diseases:
        report["failures"].append({"step": "check", "error": f"Fewer than {require_min_diseases} diseases ingested+vectorized: {diseases_vectorized}"})

    evidence_errors = 0
    evidence_top_n = min(3, top_k)

    # 5) Score each drug vs demo diseases
    for drug_id in drug_ids:
        try:
            results = _req("POST", "/score/drug_to_diseases", {
                "drug_id": drug_id,
                "disease_ids": disease_ids,
                "top_k": top_k,
            })
            top_results = results[:evidence_top_n] if isinstance(results, list) else []
            report["top_results"][drug_id] = [
                {"disease_id": r.get("disease_id"), "canonical_name": r.get("canonical_name"), "final_score": r.get("final_score")}
                for r in top_results
            ]

            # 6) Evidence for top N
            report["evidence_presence"][drug_id] = []
            for r in top_results:
                did = r.get("disease_id")
                if not did:
                    continue
                try:
                    ev = _req("GET", f"/pair/{drug_id}/{did}/evidence")
                    report["evidence_presence"][drug_id].append({"disease_id": did, "present": bool(ev)})
                except Exception as e:
                    evidence_errors += 1
                    report["evidence_presence"][drug_id].append({"disease_id": did, "present": False, "error": str(e)})

            # 7) Comparators
            try:
                comp = _req("GET", f"/drug/{drug_id}/comparators?top_k={comparators_top_k}")
                adj = comp.get("adjacent_conditions") or []
                report["comparator_condition_counts"][drug_id] = len(adj)
            except Exception as e:
                report["comparator_condition_counts"][drug_id] = 0
                report["failures"].append({"step": "comparators", "drug_id": drug_id, "error": str(e)})
        except Exception as e:
            report["failures"].append({"step": "score", "drug_id": drug_id, "error": str(e)})

    if evidence_errors > 0:
        report["failures"].append({"step": "check", "error": f"Top-{evidence_top_n} evidence call errors: {evidence_errors}"})

    out_path = os.path.join(ROOT, "artifacts", "demo_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Wrote {out_path}")

    if drugs_vectorized < require_min_drugs or diseases_vectorized < require_min_diseases or evidence_errors > 0:
        print("FAIL: demo readiness checks failed", file=sys.stderr)
        return 1
    print("PASS: demo readiness golden run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
