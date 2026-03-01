"""
Tests for Block 4 — Evidence ledger.

Deterministic. Mix of pure-function and mocked-DB tests.

Run:
    pytest tests/test_evidence_ledger.py -v
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.services.evidence_ledger import (
    build_pair_evidence,
    get_pair_evidence,
    store_pair_evidence,
)


# ---------------------------------------------------------------------------
# 1) test_build_pair_evidence_basic
# ---------------------------------------------------------------------------
class TestBuildPairEvidenceBasic(unittest.TestCase):

    def test_structure_matches_schema(self):
        drug_sparse = {"mTOR": {"weight": 0.7, "direction": -1, "evidence": []}}
        disease_sparse = {"mTOR": {"weight": 0.6, "direction": 1, "evidence": []}}
        scoring_breakdown = {
            "mechanism": {
                "score": 0.9,
                "top_nodes": [
                    {"node": "mTOR", "drug_w": 0.7, "disease_w": 0.6},
                    {"node": "NFkB", "drug_w": 0.3, "disease_w": 0.2},
                ],
            },
            "evidence": {"score": 0.4, "reasons": []},
            "safety": {"penalty": 0.0, "reasons": []},
            "uncertainty": {"penalty": 0.0, "reasons": []},
        }
        drug_short = {"pathways_top": [{"term": "mTOR signaling", "count": 4}]}
        disease_short = {"pathways_top": [{"term": "mTOR pathway", "count": 3}], "stats": {"pubs_total": 100}, "genes": ["G1"]}

        out = build_pair_evidence(
            drug_short,
            disease_short,
            drug_sparse,
            disease_sparse,
            scoring_breakdown,
            drug_id="d1",
            disease_id="dis1",
        )

        self.assertIn("drug_id", out)
        self.assertIn("disease_id", out)
        self.assertIn("generated_at", out)
        self.assertIn("mechanism_overlap", out)
        self.assertIn("pathway_triggers", out)
        self.assertIn("safety_flags", out)
        self.assertIn("trial_summary", out)
        self.assertIn("literature_summary", out)
        self.assertIn("version", out)
        self.assertEqual(out["version"], "evidence_v1")
        self.assertEqual(len(out["mechanism_overlap"]), 2)
        self.assertEqual(out["mechanism_overlap"][0]["node"], "mTOR")
        self.assertEqual(out["mechanism_overlap"][0]["overlap_weight"], 0.6)
        self.assertIn("trial_summary", out)
        self.assertIn("total_trials", out["trial_summary"])
        self.assertIn("disease_pubs_total", out["literature_summary"])


# ---------------------------------------------------------------------------
# 2) test_overlap_capped_at_5
# ---------------------------------------------------------------------------
class TestOverlapCapped(unittest.TestCase):

    def test_mechanism_overlap_capped_at_5(self):
        top_nodes = [
            {"node": f"node_{i}", "drug_w": 0.5, "disease_w": 0.4}
            for i in range(10)
        ]
        scoring_breakdown = {"mechanism": {"top_nodes": top_nodes}}
        out = build_pair_evidence({}, {}, {}, {}, scoring_breakdown)
        self.assertEqual(len(out["mechanism_overlap"]), 5)


# ---------------------------------------------------------------------------
# 3) test_safety_flags_present_when_boxed_warning
# ---------------------------------------------------------------------------
class TestSafetyFlags(unittest.TestCase):

    def test_boxed_warning_adds_safety_flag(self):
        drug_short = {"safety": {"boxed_warning": True}}
        out = build_pair_evidence(
            drug_short, {}, {}, {}, {"mechanism": {"top_nodes": []}},
            drug_id="d1", disease_id="dis1",
        )
        self.assertGreater(len(out["safety_flags"]), 0)
        types = [f["type"] for f in out["safety_flags"]]
        self.assertIn("boxed_warning", types)


# ---------------------------------------------------------------------------
# 4) test_store_and_retrieve_pair_evidence:
#    upsert twice → still 1 row
# ---------------------------------------------------------------------------
class TestStoreAndRetrieve(unittest.TestCase):

    def test_upsert_twice_still_one_row(self):
        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.first.return_value = None

        payload = {"drug_id": "d1", "disease_id": "dis1", "mechanism_overlap": [], "version": "evidence_v1"}

        r1 = store_pair_evidence(mock_db, "d1", "dis1", payload)
        self.assertIsNotNone(r1)
        mock_db.add.assert_called_once()

        # Second call: simulate existing row
        existing = MagicMock()
        existing.payload = payload
        mock_db.execute.return_value.scalars.return_value.first.return_value = existing
        mock_db.add.reset_mock()

        r2 = store_pair_evidence(mock_db, "d1", "dis1", {**payload, "mechanism_overlap": [{"x": 1}]})
        mock_db.add.assert_not_called()
        self.assertIs(r2, existing)


# ---------------------------------------------------------------------------
# 5) test_endpoint_computes_if_missing
# ---------------------------------------------------------------------------
class TestEndpointComputesIfMissing(unittest.TestCase):

    def test_endpoint_returns_evidence_when_missing(self):
        from fastapi.testclient import TestClient
        from app.db import get_db
        from app.main import app

        mock_mv = MagicMock()
        mock_mv.dense_weights = [0.5] * 25
        mock_mv.sparse = {"mTOR": {"weight": 0.5, "direction": -1, "evidence": []}}

        drug_short = {"pathways_top": [], "trials": {"total": 5}, "stats": {"pubs_total": 50}, "safety": {}}
        disease_short = {"pathways_top": [], "stats": {"pubs_total": 100}, "genes": ["G1"]}

        mock_db = MagicMock()

        def override_get_db():
            yield mock_db

        try:
            app.dependency_overrides[get_db] = override_get_db
            with patch("app.routes.evidence.get_pair_evidence", return_value=None), \
                 patch("app.routes.evidence._get_or_compute_vector", return_value=mock_mv), \
                 patch("app.routes.evidence.build_drug_short", return_value=drug_short), \
                 patch("app.routes.evidence._load_disease_short", return_value=disease_short), \
                 patch("app.routes.evidence.store_pair_evidence", return_value=MagicMock()):
                client = TestClient(app)
                r = client.get("/pair/drug-123/disease-456/evidence")
        finally:
            app.dependency_overrides.pop(get_db, None)

        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("mechanism_overlap", data)
        self.assertIn("pathway_triggers", data)
        self.assertIn("safety_flags", data)
        self.assertIn("trial_summary", data)
        self.assertIn("literature_summary", data)
        self.assertEqual(data["version"], "evidence_v1")


if __name__ == "__main__":
    unittest.main()
