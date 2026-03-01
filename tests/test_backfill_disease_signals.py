"""
Unit tests for backfill_disease_signals.  No DB, no network calls.

Three core cases:
  1. "STING-associated vasculopathy" name  → cGAS_STING pathway term added
  2. "interferonopathy" name               → cGAS_STING / typeI_interferon added
  3. Disease already has 3+ pathway_terms AND genes → backfill skipped
"""
from __future__ import annotations

import os
import sys
import unittest

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.services.disease_ingest import backfill_disease_signals


def _make_raw(
    canonical_name: str,
    pathway_terms: list | None = None,
    genes: list | None = None,
    synonyms: list | None = None,
) -> dict:
    """Minimal raw disease summary fixture."""
    return {
        "disease_id": "test-id",
        "canonical_name": canonical_name,
        "synonyms": synonyms or [],
        "genes": genes or [],
        "phenotype_terms": [],
        "pathway_terms": pathway_terms or [],
        "clinvar": {"by_significance": {}, "top_genes": []},
        "publications": {"total": 0, "by_year": {}, "recent": []},
        "source_status": {},
        "errors": [],
    }


def _pathway_term_names(raw: dict) -> list[str]:
    return [p.get("term", "") for p in (raw.get("pathway_terms") or []) if isinstance(p, dict)]


def _gene_symbols(raw: dict) -> list[str]:
    genes = raw.get("genes") or []
    return [
        (g.get("symbol") if isinstance(g, dict) else str(g)).strip()
        for g in genes
    ]


class TestBackfillSTING(unittest.TestCase):
    """'STING-associated vasculopathy' should trigger cGAS_STING backfill."""

    def setUp(self) -> None:
        self.raw = _make_raw("STING-associated vasculopathy")
        self.result = backfill_disease_signals(self.raw)

    def test_backfill_applied(self) -> None:
        self.assertEqual(self.result["source_status"]["backfill"], "applied")

    def test_cgas_sting_alias_in_pathway_terms(self) -> None:
        terms = _pathway_term_names(self.result)
        # At least one term should match the 'sting' alias family
        matched = any("sting" in t.lower() for t in terms)
        self.assertTrue(matched, f"Expected sting-related term in {terms}")

    def test_pathway_terms_have_backfill_source(self) -> None:
        backfill_terms = [
            p for p in (self.result.get("pathway_terms") or [])
            if isinstance(p, dict) and p.get("source") == "backfill"
        ]
        self.assertGreater(len(backfill_terms), 0)

    def test_pathway_terms_count_reasonable(self) -> None:
        self.assertLessEqual(len(self.result.get("pathway_terms") or []), 14)

    def test_deterministic(self) -> None:
        raw2 = _make_raw("STING-associated vasculopathy")
        result2 = backfill_disease_signals(raw2)
        self.assertEqual(
            _pathway_term_names(self.result),
            _pathway_term_names(result2),
        )


class TestBackfillInterferonopathy(unittest.TestCase):
    """'Interferonopathy' name should add both cGAS_STING and typeI_interferon terms."""

    def setUp(self) -> None:
        self.raw = _make_raw("Interferonopathy")
        self.result = backfill_disease_signals(self.raw)

    def test_backfill_applied(self) -> None:
        self.assertEqual(self.result["source_status"]["backfill"], "applied")

    def test_interferonopathy_alias_found(self) -> None:
        terms = _pathway_term_names(self.result)
        # 'interferonopathy' is an alias for cGAS_STING
        matched = any("interferonopathy" in t.lower() for t in terms)
        self.assertTrue(matched, f"Expected interferonopathy alias in {terms}")

    def test_interferon_alias_found(self) -> None:
        terms = _pathway_term_names(self.result)
        # 'interferon' is an alias for typeI_interferon
        # It appears as a substring of 'interferonopathy' so should also match
        matched = any("interferon" in t.lower() for t in terms)
        self.assertTrue(matched, f"Expected interferon-related term in {terms}")

    def test_multiple_nodes_backfilled(self) -> None:
        backfill_terms = [
            p for p in (self.result.get("pathway_terms") or [])
            if isinstance(p, dict) and p.get("source") == "backfill"
        ]
        # Both cGAS_STING and typeI_interferon should be covered
        self.assertGreaterEqual(len(backfill_terms), 2)


class TestBackfillStatGeneBackfill(unittest.TestCase):
    """'STAT1 gain-of-function disease' should backfill STAT1 gene and JAK_STAT pathway."""

    def setUp(self) -> None:
        self.raw = _make_raw("STAT1 gain-of-function disease")
        self.result = backfill_disease_signals(self.raw)

    def test_backfill_applied(self) -> None:
        self.assertEqual(self.result["source_status"]["backfill"], "applied")

    def test_stat1_alias_in_pathway_terms(self) -> None:
        terms = _pathway_term_names(self.result)
        # JAK_STAT aliases include "stat" which matches as a substring of "stat1".
        # The first alias that fires is "stat", so the term added is "stat".
        matched = any("stat" in t.lower() for t in terms)
        self.assertTrue(matched, f"Expected stat-related term in {terms}")

    def test_stat1_gene_backfilled(self) -> None:
        genes = _gene_symbols(self.result)
        self.assertIn("STAT1", genes, f"Expected STAT1 gene in {genes}")


class TestBackfillSkipped(unittest.TestCase):
    """Disease with ≥3 pathway terms AND non-empty genes should be skipped."""

    def test_skipped_when_sufficient_data(self) -> None:
        raw = _make_raw(
            "Some disease with no keyword",
            pathway_terms=[
                {"term": "autophagy", "count": 3, "source": "keyword_dict"},
                {"term": "apoptosis", "count": 2, "source": "keyword_dict"},
                {"term": "mTOR", "count": 1, "source": "keyword_dict"},
            ],
            genes=[{"symbol": "ATM", "source": "text"}],
        )
        result = backfill_disease_signals(raw)
        self.assertEqual(result["source_status"]["backfill"], "skipped")

    def test_pathway_terms_unchanged_when_skipped(self) -> None:
        original_terms = [
            {"term": "autophagy", "count": 3, "source": "keyword_dict"},
            {"term": "apoptosis", "count": 2, "source": "keyword_dict"},
            {"term": "mTOR", "count": 1, "source": "keyword_dict"},
        ]
        raw = _make_raw(
            "Some disease",
            pathway_terms=original_terms,
            genes=[{"symbol": "ATM", "source": "text"}],
        )
        result = backfill_disease_signals(raw)
        self.assertEqual(len(result["pathway_terms"]), len(original_terms))

    def test_skipped_also_when_no_corpus_match(self) -> None:
        """Disease name with no alias match and no gene symbol → skipped or applied(no hits)."""
        raw = _make_raw("Xylophagous rare condition no keyword")
        result = backfill_disease_signals(raw)
        # backfill runs (pathway_terms < 3) but may not find anything
        # status should be either "applied" (0 terms added still runs) or "skipped"
        self.assertIn(result["source_status"]["backfill"], ("applied", "skipped"))


class TestBackfillEdgeCases(unittest.TestCase):

    def test_empty_canonical_name(self) -> None:
        raw = _make_raw("")
        result = backfill_disease_signals(raw)
        self.assertEqual(result["source_status"]["backfill"], "skipped")

    def test_existing_backfill_terms_not_duplicated(self) -> None:
        raw = _make_raw(
            "STING-associated vasculopathy",
            pathway_terms=[{"term": "sting", "count": 5, "source": "keyword_dict"}],
        )
        result = backfill_disease_signals(raw)
        terms = _pathway_term_names(result)
        # "sting" should not appear twice
        sting_count = sum(1 for t in terms if t.lower() == "sting")
        self.assertEqual(sting_count, 1, f"Duplicate 'sting' in {terms}")

    def test_cap_at_twelve_new_terms(self) -> None:
        # Very generic name that matches many aliases — still capped at 12 new terms
        raw = _make_raw(
            "JAK STAT interferon NLRP3 STING mTOR apoptosis autophagy collagen fibrosis"
        )
        result = backfill_disease_signals(raw)
        backfill_terms = [
            p for p in (result.get("pathway_terms") or [])
            if isinstance(p, dict) and p.get("source") == "backfill"
        ]
        self.assertLessEqual(len(backfill_terms), 12)

    def test_backfill_term_count_is_two(self) -> None:
        raw = _make_raw("STING-associated vasculopathy")
        result = backfill_disease_signals(raw)
        for p in result.get("pathway_terms") or []:
            if isinstance(p, dict) and p.get("source") == "backfill":
                self.assertEqual(p.get("count"), 2)


if __name__ == "__main__":
    unittest.main()
