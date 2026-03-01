"""
Tests for Block 6 — Direction-aware causal compatibility.

Deterministic.
"""
from __future__ import annotations

import os
import sys
import unittest

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.services.mechanism_vocab import MECH_NODES
from app.services.scoring import direction_compatibility, score_pair


# ---------------------------------------------------------------------------
# 1) Drug inhibitor (-1) vs disease overactivation (+1) → positive direction_score
# ---------------------------------------------------------------------------
class TestInhibitorVsOveractivation(unittest.TestCase):

    def test_positive_direction_score(self):
        drug_sparse = {"mTOR": {"weight": 0.8, "direction": -1, "evidence": []}}
        disease_sparse = {"mTOR": {"weight": 0.6, "direction": 1, "evidence": []}}
        out = direction_compatibility(drug_sparse, disease_sparse)
        self.assertGreater(out["direction_score"], 0)


# ---------------------------------------------------------------------------
# 2) Drug inhibitor (-1) vs disease deficiency (-1) → negative direction_score
# ---------------------------------------------------------------------------
class TestInhibitorVsDeficiency(unittest.TestCase):

    def test_negative_direction_score(self):
        drug_sparse = {"mTOR": {"weight": 0.8, "direction": -1, "evidence": []}}
        disease_sparse = {"mTOR": {"weight": 0.6, "direction": -1, "evidence": []}}
        out = direction_compatibility(drug_sparse, disease_sparse)
        self.assertLess(out["direction_score"], 0)


# ---------------------------------------------------------------------------
# 3) Unknown direction → zero contribution
# ---------------------------------------------------------------------------
class TestUnknownDirection(unittest.TestCase):

    def test_zero_contribution_when_unknown(self):
        drug_sparse = {"mTOR": {"weight": 0.8, "direction": 0, "evidence": []}}
        disease_sparse = {"mTOR": {"weight": 0.6, "direction": 0, "evidence": []}}
        out = direction_compatibility(drug_sparse, disease_sparse)
        self.assertEqual(out["direction_score"], 0.0)


# ---------------------------------------------------------------------------
# 4) Final score increases when direction positive
# ---------------------------------------------------------------------------
class TestFinalScoreDirection(unittest.TestCase):

    def test_final_score_higher_with_positive_direction(self):
        vec = [0.5] * len(MECH_NODES)
        sparse = {"mTOR": {"weight": 0.5, "direction": 0, "evidence": []}}
        d_short = {"trials": {"total": 5}, "stats": {"pubs_total": 60}, "safety": {}}
        dis_short = {"genes": ["G1"], "stats": {"pubs_total": 100}}

        # Compatible: drug -1, disease +1
        drug_s = {"mTOR": {"weight": 0.5, "direction": -1, "evidence": []}}
        disease_s = {"mTOR": {"weight": 0.5, "direction": 1, "evidence": []}}
        score_compat = score_pair(d_short, dis_short, vec, vec, drug_s, disease_s)

        # Incompatible: same direction
        drug_s2 = {"mTOR": {"weight": 0.5, "direction": -1, "evidence": []}}
        disease_s2 = {"mTOR": {"weight": 0.5, "direction": -1, "evidence": []}}
        score_incompat = score_pair(d_short, dis_short, vec, vec, drug_s2, disease_s2)

        self.assertGreater(score_compat["final_score"], score_incompat["final_score"])


# ---------------------------------------------------------------------------
# 5) Node_effects list matches overlapping nodes
# ---------------------------------------------------------------------------
class TestNodeEffects(unittest.TestCase):

    def test_node_effects_list_matches_overlap(self):
        drug_sparse = {
            "mTOR": {"weight": 0.8, "direction": -1, "evidence": []},
            "NFkB": {"weight": 0.4, "direction": 0, "evidence": []},
        }
        disease_sparse = {
            "mTOR": {"weight": 0.6, "direction": 1, "evidence": []},
            "NFkB": {"weight": 0.4, "direction": 0, "evidence": []},
        }
        out = direction_compatibility(drug_sparse, disease_sparse)
        nodes = [e["node"] for e in out["node_effects"]]
        self.assertIn("mTOR", nodes)
        self.assertIn("NFkB", nodes)
        self.assertEqual(len(out["node_effects"]), 2)


if __name__ == "__main__":
    unittest.main()
