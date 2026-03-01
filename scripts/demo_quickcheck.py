#!/usr/bin/env python3
"""
Demo quickcheck: seeds bootstrap data, runs scoring for Ruxolitinib and
Sirolimus, and asserts that at least one disease has a non-zero mechanism
match for each drug.

Usage:
    python scripts/demo_quickcheck.py [--base-url http://localhost:8000]

Exit codes:
    0  PASS — all assertions satisfied
    1  FAIL — one or more assertions failed or server unreachable
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package not installed. Run: pip install requests")
    sys.exit(1)

DEMO_DRUGS = ["Ruxolitinib", "Sirolimus"]


def _post(base_url: str, path: str, payload: dict) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    try:
        r = requests.post(url, json=payload, timeout=300)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Cannot connect to {url}. Is the server running?")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: HTTP {e.response.status_code} from {url}: {e.response.text[:200]}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def run_quickcheck(base_url: str) -> bool:
    all_pass = True

    # 1. Seed bootstrap data
    print("==> POST /bootstrap/seed ...")
    seed_result = _post(base_url, "/bootstrap/seed", {})
    ok = seed_result.get("ok", False)
    drugs_ok = sum(
        1 for d in (seed_result.get("drugs") or [])
        if d.get("status") in ("ok", "skipped") and d.get("drug_id")
    )
    diseases_ok = sum(
        1 for d in (seed_result.get("diseases") or [])
        if d.get("status") in ("ok", "skipped") and d.get("disease_id")
    )
    print(f"    seed ok={ok}  drugs_ready={drugs_ok}  diseases_ready={diseases_ok}")
    if seed_result.get("errors"):
        print(f"    seed warnings: {seed_result['errors'][:3]}")

    # 2. Run scoring for each demo drug
    for drug_name in DEMO_DRUGS:
        print(f"\n==> POST /bootstrap/run  drug={drug_name} ...")
        run_result = _post(
            base_url,
            "/bootstrap/run",
            {"drug_name": drug_name, "top_k": 10},
        )

        scored: list[dict[str, Any]] = run_result.get("scored_diseases") or []
        total = len(scored)

        # Find diseases with mechanism.score > 0 AND len(top_nodes) >= 1
        mech_hits = [
            d for d in scored
            if float(((d.get("breakdown") or {}).get("mechanism") or {}).get("score") or 0) > 0
            and len((d.get("top_nodes") or [])) >= 1
        ]

        passed = len(mech_hits) >= 1
        status = "PASS" if passed else "FAIL"
        print(f"    {status}  scored={total}  mechanism_nonzero={len(mech_hits)}")

        # Print top 3 diseases
        for rank, entry in enumerate(scored[:3], 1):
            cname = entry.get("canonical_name") or entry.get("disease_id") or "?"
            fscore = round(float(entry.get("final_score") or 0), 4)
            nodes = entry.get("top_nodes") or []
            node_str = ", ".join(n.get("node", "") for n in nodes[:3]) or "(none)"
            mz = entry.get("mechanism_nonzero", False)
            print(f"    #{rank} {cname!r:45s}  final={fscore:.4f}  mech_nonzero={mz}  nodes=[{node_str}]")

        if not passed:
            all_pass = False
            print(f"    FAIL: no disease had mechanism.score > 0 for {drug_name!r}")

    return all_pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Demo mechanism-match quickcheck")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the running API server (default: http://localhost:8000)",
    )
    args = parser.parse_args()

    print(f"Drug-Intel Platform — demo quickcheck  [{args.base_url}]")
    print("-" * 60)

    passed = run_quickcheck(args.base_url)

    print("\n" + "=" * 60)
    if passed:
        print("OVERALL: PASS")
        sys.exit(0)
    else:
        print("OVERALL: FAIL")
        sys.exit(1)


if __name__ == "__main__":
    main()
