#!/usr/bin/env python3
"""
Bootstrap acceptance: verify drug→disease matching produces signal.

Runs against a live API. Requires ENABLE_BOOTSTRAP_ROUTES=true.

Usage:
  ENABLE_BOOTSTRAP_ROUTES=true uvicorn app.main:app &
  python scripts/bootstrap_acceptance.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
ANCHOR_DRUGS = ["Ruxolitinib", "Rapamycin", "Eculizumab"]
SCORE_THRESHOLD = 0.01 if os.environ.get("SCORING_DEMO_MODE", "").lower() in {"1", "true", "yes", "on"} else 0.05


def _parse_env_drugs() -> list[str]:
    val = os.environ.get("BOOTSTRAP_ACCEPT_DRUGS")
    if not val:
        return list(ANCHOR_DRUGS)
    seen = set()
    out = []
    for part in val.split(","):
        s = part.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _req(method: str, path: str, json_body: dict | None = None) -> tuple[int, dict]:
    import requests
    url = f"{BASE_URL}{path}"
    try:
        r = requests.request(method, url, json=json_body, timeout=120)
        data = r.json() if r.content else {}
        return r.status_code, data
    except Exception as e:
        return -1, {"_error": str(e)}


def main() -> int:
    drugs = _parse_env_drugs()

    # 1) Check bootstrap routes enabled
    code, _ = _req("POST", "/bootstrap/seed", {})
    if code == -1:
        print(f"Error: Cannot reach {BASE_URL}. Is the API running?", file=sys.stderr)
        return 1
    if code == 404:
        print(
            "Error: Bootstrap routes not enabled. Start API with:\n"
            "  ENABLE_BOOTSTRAP_ROUTES=true uvicorn app.main:app",
            file=sys.stderr,
        )
        return 1

    # 2) Seed
    code, seed_resp = _req("POST", "/bootstrap/seed", {})
    if code != 200:
        print(f"Error: POST /bootstrap/seed failed: {code} {seed_resp}", file=sys.stderr)
        return 1
    if not seed_resp.get("ok"):
        print(f"Error: Bootstrap seed returned ok=false: {seed_resp.get('errors', [])}", file=sys.stderr)
        return 1

    all_pass = True
    for drug in drugs:
        code, run_resp = _req("POST", "/bootstrap/run", {
            "drug_name": drug,
            "top_k": 10,
            "restrict_to_bootstrap_diseases": True,
        })
        if code != 200:
            print(f"DRUG: {drug}\n  FAIL: {code} {run_resp}")
            all_pass = False
            continue

        scored = run_resp.get("scored_diseases") or []
        drug_id = (run_resp.get("drug") or {}).get("drug_id") or ""

        n_scored = len(scored)
        n_above = sum(1 for s in scored if float(s.get("final_score", 0)) > SCORE_THRESHOLD)
        top = scored[0] if scored else {}
        top_nodes = top.get("top_nodes") or []

        # Top 3 why_summary length >= 3
        why_ok = len(scored) >= 3 and all(len((s.get("why_summary") or [])) >= 3 for s in scored[:3])

        print(f"DRUG: {drug}")
        print(f"  scored: {n_scored}")
        print(f"  >{SCORE_THRESHOLD} count: {n_above}")
        print(f"  top_nodes: {top_nodes[:3]}...")

        evidence_ok = 0
        evidence_total = min(2, len(scored))
        for i in range(evidence_total):
            item = scored[i]
            ev_url = item.get("evidence_url") or ""
            dis_id = item.get("disease_id") or ""
            if not ev_url:
                ev_url = f"/pair/{drug_id}/{dis_id}/evidence"
            c, ev_data = _req("GET", ev_url)
            if c != 200:
                continue
            mech = (ev_data.get("mechanism_overlap") or [])
            direction = ev_data.get("direction_summary")
            prov = ev_data.get("provenance") or {}
            if (len(mech) >= 1 or direction) and isinstance(prov.get("drug_sources"), list) and isinstance(prov.get("disease_sources"), list):
                evidence_ok += 1

        print(f"  evidence_ok: {evidence_ok}/{evidence_total}")

        comp_ok = False
        comp_similar = 0
        comp_adjacent = 0
        if scored:
            comp_url = scored[0].get("comparators_url") or f"/drug/{drug_id}/comparators?top_k=10"
            c, comp_data = _req("GET", comp_url)
            comp_ok = c == 200
            if comp_ok:
                comp_similar = len(comp_data.get("similar_drugs") or [])
                comp_adjacent = len(comp_data.get("adjacent_conditions") or [])

        print(f"  comparators_ok: {comp_ok} (similar_drugs={comp_similar}, adjacent_conditions={comp_adjacent})")

        checks = [
            n_scored >= 6,
            n_above >= 3,
            len(top_nodes) >= 1,
            why_ok,
            evidence_ok >= evidence_total,
            comp_ok,
        ]
        passed = all(checks)
        if not passed:
            all_pass = False
        print(f"  {'PASS' if passed else 'FAIL'}")
        print()

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
