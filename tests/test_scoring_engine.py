"""
Tests for Block 3 — scoring engine.

Pure functions; deterministic. No live DB required for these tests.

Run:
    pytest tests/test_scoring_engine.py -v
"""
from __future__ import annotations

import os
import sys
import unittest

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.services.mechanism_vocab import MECH_NODES
from app.services.scoring import (
    DEFAULT_WEIGHTS,
    evidence_score,
    mechanism_score,
    safety_penalty,
    score_pair,
    uncertainty_penalty,
)


def _dense(n: int, fill: float = 0.0) -> list[float]:
    """Dense vector of length len(MECH_NODES) with first n indices set to fill."""
    out = [0.0] * len(MECH_NODES)
    for i in range(min(n, len(out))):
        out[i] = fill
    return out


# ---------------------------------------------------------------------------
# 1) mechanism_only_scoring: identical vectors → mechanism score ~1
# ---------------------------------------------------------------------------
class TestMechanismOnlyScoring(unittest.TestCase):

    def test_identical_vectors_mechanism_score_near_one(self):
        vec = [0.5] * len(MECH_NODES)
        result = mechanism_score(vec, vec)
        self.assertIn("score", result)
        self.assertIn("top_nodes", result)
        self.assertGreaterEqual(result["score"], 0.99)
        self.assertLessEqual(result["score"], 1.0)

    def test_orthogonal_vectors_mechanism_score_zero(self):
        a = [1.0] + [0.0] * (len(MECH_NODES) - 1)
        b = [0.0] * (len(MECH_NODES) - 1) + [1.0]
        result = mechanism_score(a, b)
        self.assertEqual(result["score"], 0.0)


# ---------------------------------------------------------------------------
# 2) safety_penalty_applied: mock drug_short with boxed warning → penalty > 0
# ---------------------------------------------------------------------------
class TestSafetyPenalty(unittest.TestCase):

    def test_boxed_warning_adds_penalty(self):
        drug_short = {"safety": {"boxed_warning": "Major warning text"}}
        out = safety_penalty(drug_short)
        self.assertGreater(out["penalty"], 0)
        self.assertIn("boxed_warning", out["reasons"])

    def test_empty_drug_short_zero_penalty(self):
        out = safety_penalty({})
        self.assertEqual(out["penalty"], 0.0)
        self.assertEqual(out["reasons"], [])


# ---------------------------------------------------------------------------
# 3) uncertainty_penalty_empty_vectors: empty sparse → penalty applied
# ---------------------------------------------------------------------------
class TestUncertaintyPenalty(unittest.TestCase):

    def test_empty_sparse_vectors_add_penalty(self):
        out = uncertainty_penalty(
            {},
            {},
            {},  # drug_sparse empty
            {},  # disease_sparse empty
        )
        self.assertGreater(out["penalty"], 0)
        self.assertIn("drug_vector_empty", out["reasons"])
        self.assertIn("disease_vector_empty", out["reasons"])

    def test_non_empty_sparse_lower_penalty(self):
        drug_s = {"NFkB": {"weight": 0.8, "direction": 1, "evidence": []}}
        disease_s = {"NFkB": {"weight": 0.6, "direction": 1, "evidence": []}}
        out = uncertainty_penalty(
            {},
            {"genes": ["BRCA1"], "stats": {"pubs_total": 100}},
            drug_s,
            disease_s,
        )
        self.assertNotIn("drug_vector_empty", out["reasons"])
        self.assertNotIn("disease_vector_empty", out["reasons"])


# ---------------------------------------------------------------------------
# 4) ranking_order: two disease vectors, one more similar → ranked first
# ---------------------------------------------------------------------------
class TestRankingOrder(unittest.TestCase):

    def test_more_similar_disease_scores_higher(self):
        drug_dense = [0.5, 0.5, 0.0] + [0.0] * (len(MECH_NODES) - 3)
        disease_a_dense = [0.5, 0.5, 0.0] + [0.0] * (len(MECH_NODES) - 3)  # high overlap
        disease_b_dense = [0.0, 0.0, 0.5] + [0.0] * (len(MECH_NODES) - 3)  # low overlap
        drug_s = {"cGAS_STING": {"weight": 0.5}, "typeI_interferon": {"weight": 0.5}}
        disease_a_s = {"cGAS_STING": {"weight": 0.5}, "typeI_interferon": {"weight": 0.5}}
        disease_b_s = {"JAK_STAT": {"weight": 0.5}}
        empty_short = {}
        d_short = {"trials": {"total": 5}, "stats": {"pubs_total": 60}}
        score_a = score_pair(
            d_short, {"genes": ["G1"], "stats": {"pubs_total": 100}},
            drug_dense, disease_a_dense,
            drug_s, disease_a_s,
        )
        score_b = score_pair(
            d_short, {"genes": ["G1"], "stats": {"pubs_total": 100}},
            drug_dense, disease_b_dense,
            drug_s, disease_b_s,
        )
        self.assertGreater(score_a["final_score"], score_b["final_score"])


# ---------------------------------------------------------------------------
# 5) weights_override: custom weights → affects final_score
# ---------------------------------------------------------------------------
class TestWeightsOverride(unittest.TestCase):

    def test_zero_safety_weight_ignores_penalty(self):
        drug_short = {"safety": {"boxed_warning": "Warning"}}
        disease_short = {"genes": ["G1"], "stats": {"pubs_total": 100}}
        vec = [0.3] * len(MECH_NODES)
        sparse = {"NFkB": {"weight": 0.3}}
        weights_full = DEFAULT_WEIGHTS.copy()
        weights_zero_safety = {**DEFAULT_WEIGHTS, "safety": 0.0}
        out_full = score_pair(
            drug_short, disease_short,
            vec, vec,
            sparse, sparse,
            weights=weights_full,
        )
        out_zero_safety = score_pair(
            drug_short, disease_short,
            vec, vec,
            sparse, sparse,
            weights=weights_zero_safety,
        )
        self.assertGreater(out_zero_safety["final_score"], out_full["final_score"])
