"""
Tests for build_why_summary. No DB/network.
"""
from __future__ import annotations

import os
import sys
import unittest

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)


class TestWhySummary(unittest.TestCase):

    def test_build_why_summary_top_overlap(self) -> None:
        from app.services.bootstrap_seed import build_why_summary
        breakdown = {
            "mechanism": {"top_nodes": [{"node": "JAK_STAT", "drug_w": 0.8, "disease_w": 0.7}]},
            "direction": {"node_effects": []},
            "safety": {"penalty": 0},
        }
        drug_short = {"pathways_top": [{"term": "JAK STAT", "count": 2}], "targets_top": [{"gene": "JAK2"}], "trials": {"total": 5}, "stats": {"pubs_total": 100}, "safety": {"boxed_warning": False}}
        disease_short = {"stats": {"pubs_total": 50}}
        drug_sparse = {"JAK_STAT": {"weight": 0.8, "evidence": []}}
        top_nodes = [{"node": "JAK_STAT", "drug_w": 0.8, "disease_w": 0.7}]
        lines = build_why_summary(breakdown, drug_short, disease_short, drug_sparse, top_nodes)
        self.assertIsInstance(lines, list)
        self.assertTrue(any("Top overlap" in l and "JAK_STAT" in l for l in lines))
        self.assertTrue(any("Evidence:" in l for l in lines))
        self.assertTrue(any("Safety:" in l for l in lines))

    def test_build_why_summary_direction_supportive(self) -> None:
        from app.services.bootstrap_seed import build_why_summary
        breakdown = {
            "mechanism": {"top_nodes": [{"node": "mTOR", "drug_w": 0.6, "disease_w": 0.5}]},
            "direction": {"node_effects": [{"node": "mTOR", "drug_dir": -1, "disease_dir": 1, "effect": 0.3}]},
            "safety": {"penalty": 0},
        }
        drug_short = {"pathways_top": [], "trials": {"total": 0}, "stats": {}, "safety": {"boxed_warning": False}}
        disease_short = {"stats": {}}
        drug_sparse = {"mTOR": {"weight": 0.6}}
        top_nodes = [{"node": "mTOR", "drug_w": 0.6, "disease_w": 0.5}]
        lines = build_why_summary(breakdown, drug_short, disease_short, drug_sparse, top_nodes)
        self.assertTrue(any("supportive" in l.lower() for l in lines))

    def test_build_why_summary_empty_inputs(self) -> None:
        from app.services.bootstrap_seed import build_why_summary
        lines = build_why_summary({}, {}, {}, {}, [])
        self.assertIsInstance(lines, list)
        self.assertTrue(any("Evidence:" in l for l in lines))
        self.assertTrue(any("Safety:" in l for l in lines))
