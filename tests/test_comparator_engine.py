"""
Tests for Block 5 — Comparator engine + node tiering.

Deterministic. Uses mocks for DB.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.services.comparator_engine import get_adjacent_conditions, get_similar_drugs
from app.services.node_tiering import compute_node_tiers


# ---------------------------------------------------------------------------
# 1) Similarity excludes self
# ---------------------------------------------------------------------------
class TestSimilarityExcludesSelf(unittest.TestCase):

    def test_self_excluded(self):
        mock_db = MagicMock()
        mock_mv = MagicMock()
        mock_mv.entity_id = "drug-a"
        mock_mv.dense_weights = [0.5] * 25
        mock_mv.sparse = {"mTOR": {"weight": 0.5}}

        other_mv = MagicMock()
        other_mv.entity_id = "drug-b"
        other_mv.dense_weights = [0.5] * 25
        other_mv.sparse = {"mTOR": {"weight": 0.5}}

        mock_db.execute.return_value.scalars.return_value.first.return_value = mock_mv
        mock_db.execute.return_value.scalars.return_value.all.return_value = [other_mv]
        mock_db.get.return_value = MagicMock(canonical_name="Drug B")

        with unittest.mock.patch("app.services.comparator_engine.build_drug_short", return_value={"trials": {"total": 5}, "safety": {}}):
            result = get_similar_drugs("drug-a", mock_db, top_k=10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["drug_id"], "drug-b")
        self.assertNotIn("drug-a", [r["drug_id"] for r in result])


# ---------------------------------------------------------------------------
# 2) Returns top_k correctly
# ---------------------------------------------------------------------------
class TestReturnsTopK(unittest.TestCase):

    def test_respects_top_k(self):
        mock_db = MagicMock()
        mock_mv = MagicMock()
        mock_mv.entity_id = "drug-a"
        mock_mv.dense_weights = [0.3] * 25
        mock_mv.sparse = {"mTOR": {"weight": 0.3}}

        others = []
        for i in range(5):
            omv = MagicMock()
            omv.entity_id = f"drug-{i}"
            omv.dense_weights = [0.3] * 25
            omv.sparse = {"mTOR": {"weight": 0.3}}
            others.append(omv)

        mock_db.execute.return_value.scalars.return_value.first.return_value = mock_mv
        mock_db.execute.return_value.scalars.return_value.all.return_value = others
        mock_db.get.return_value = MagicMock(canonical_name="Drug")

        with unittest.mock.patch("app.services.comparator_engine.build_drug_short", return_value={"trials": {"total": 0}, "safety": {}}):
            result = get_similar_drugs("drug-a", mock_db, top_k=3)
        self.assertEqual(len(result), 3)


# ---------------------------------------------------------------------------
# 3) Adjacent conditions aggregated correctly
# ---------------------------------------------------------------------------
class TestAdjacentConditions(unittest.TestCase):

    def test_conditions_aggregated(self):
        mock_db = MagicMock()
        similar_drugs = [
            {"drug_id": "d1"},
            {"drug_id": "d2"},
        ]

        def mock_raw(drug_id, db):
            if drug_id == "d1":
                return {"trials": {"trials": [{"conditions": ["Diabetes", " obesity"]}]}}
            if drug_id == "d2":
                return {"trials": {"trials": [{"conditions": ["diabetes", "hypertension"]}]}}
            return None

        with unittest.mock.patch("app.services.comparator_engine.build_drug_raw_summary", side_effect=mock_raw):
            result = get_adjacent_conditions(similar_drugs, mock_db, top_k=15)
        self.assertGreater(len(result), 0)
        conds = {r["condition"]: r["count"] for r in result}
        self.assertIn("diabetes", conds)
        self.assertEqual(conds["diabetes"], 2)


# ---------------------------------------------------------------------------
# 4) Node tier increases when trial count > 0
# ---------------------------------------------------------------------------
class TestNodeTierTrialCount(unittest.TestCase):

    def test_tier_2_when_trials_gt_0(self):
        drug_short = {
            "pathways_top": [{"term": "mTOR pathway", "count": 5}],
            "targets_top": [],
            "trials": {"total": 1, "phase_counts": {}},
        }
        node_weights = {"mTOR": {"weight": 0.5}}
        result = compute_node_tiers(drug_short, node_weights)
        self.assertIn("mTOR", result)
        self.assertGreaterEqual(result["mTOR"]["tier"], 2)
        self.assertIn("trial_exposure", result["mTOR"]["support"])


# ---------------------------------------------------------------------------
# 5) Node tier increases when ≥3 trials
# ---------------------------------------------------------------------------
class TestNodeTierThreeTrials(unittest.TestCase):

    def test_tier_3_when_three_trials(self):
        drug_short = {
            "pathways_top": [{"term": "mTOR", "count": 3}],
            "targets_top": [{"gene": "MTOR"}],
            "trials": {"total": 5, "phase_counts": {}},
        }
        node_weights = {"mTOR": {"weight": 0.6}}
        result = compute_node_tiers(drug_short, node_weights)
        self.assertIn("mTOR", result)
        self.assertEqual(result["mTOR"]["tier"], 3)


if __name__ == "__main__":
    unittest.main()
