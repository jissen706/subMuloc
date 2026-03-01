"""
Tests for Block 7 — validation_engine metrics and recommendations.

Uses synthetic metrics; does not hit a real DB.
"""
from __future__ import annotations

import os
import sys
import unittest

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.services.validation_engine import (
    compute_recommendations,
    format_data_health,
)


class TestValidationRecommendations(unittest.TestCase):

    def test_detects_score_collapse(self):
        metrics = {
            "score_std": 0.01,
            "percent_zero_scores": 0.0,
            "percent_negative_scores": 0.0,
            "direction_weight_effect": 0.0,
            "mechanism_score_mean": 0.2,
        }
        recs = compute_recommendations(metrics)
        self.assertTrue(any("Score collapse detected" in r for r in recs))

    def test_detects_high_direction_dominance(self):
        metrics = {
            "score_std": 0.1,
            "percent_zero_scores": 0.0,
            "percent_negative_scores": 0.0,
            "direction_weight_effect": 0.3,
            "mechanism_score_mean": 0.2,
        }
        recs = compute_recommendations(metrics)
        self.assertTrue(any("Direction overpowering similarity" in r for r in recs))

    def test_detects_excessive_zero_scores(self):
        metrics = {
            "score_std": 0.1,
            "percent_zero_scores": 50.0,
            "percent_negative_scores": 0.0,
            "direction_weight_effect": 0.0,
            "mechanism_score_mean": 0.2,
        }
        recs = compute_recommendations(metrics)
        self.assertTrue(any("Excessive zero scores" in r for r in recs))

    def test_detects_excessive_negative_scores(self):
        metrics = {
            "score_std": 0.1,
            "percent_zero_scores": 0.0,
            "percent_negative_scores": 40.0,
            "direction_weight_effect": 0.0,
            "mechanism_score_mean": 0.2,
        }
        recs = compute_recommendations(metrics)
        self.assertTrue(any("Excessive negative scoring" in r for r in recs))

    def test_returns_default_ok_message(self):
        metrics = {
            "score_std": 0.1,
            "percent_zero_scores": 5.0,
            "percent_negative_scores": 5.0,
            "direction_weight_effect": 0.05,
            "mechanism_score_mean": 0.3,
        }
        recs = compute_recommendations(metrics)
        self.assertEqual(len(recs), 1)
        self.assertIn("Scoring health looks acceptable", recs[0])


class TestDataHealthFormatting(unittest.TestCase):
    """Smoke test: compute_recommendations + data_health formatting (no DB)."""

    def test_compute_recommendations_smoke(self):
        recs = compute_recommendations({"score_std": 0.1, "percent_zero_scores": 5.0})
        self.assertIsInstance(recs, list)
        self.assertTrue(all(isinstance(r, str) for r in recs))

    def test_format_data_health_smoke(self):
        raw = {
            "vectorized_drugs_count": 5,
            "vectorized_diseases_count": 10,
            "drugs_missing_short_summary": 0,
            "diseases_missing_short_summary": 1,
        }
        out = format_data_health(raw)
        self.assertIsInstance(out, dict)
        self.assertIn("vectorized_drugs_count", out)
        self.assertEqual(out["vectorized_drugs_count"], 5)
        self.assertEqual(out["vectorized_diseases_count"], 10)
        for k, v in out.items():
            self.assertIsInstance(v, int, msg=f"{k} should be int")


if __name__ == "__main__":
    unittest.main()

