"""
Tests for disease_summary_compactor.compact_disease_summary (deterministic, caps).
"""
from __future__ import annotations

import os
import sys
import unittest

if __name__ == "__main__":
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

from app.services.disease_summary_compactor import compact_disease_summary


# Fixture A: gene-first (NGLY1-like) with clinvar bins + pubs
FIXTURE_GENE_FIRST = {
    "disease_id": "id-ngly1",
    "canonical_name": "NGLY1 deficiency",
    "ids": {"orpha": "404454", "omim": "615273"},
    "synonyms": ["NGLY1 deficiency", "Congenital disorder of deglycosylation"],
    "genes": [
        {"symbol": "NGLY1", "source": "heuristic"},
        {"symbol": "ENG", "source": "text"},
    ],
    "phenotype_terms": [
        {"term": "hypotonia", "count": 10, "source": "text"},
        {"term": "disease", "count": 5, "source": "text"},
        {"term": "seizures", "count": 8, "source": "text"},
    ],
    "pathway_terms": [
        {"term": "ubiquitin", "count": 12, "source": "keyword_dict"},
        {"term": "pathway", "count": 3, "source": "keyword_dict"},
    ],
    "clinvar": {
        "by_significance": {
            "Pathogenic": 5,
            "Likely_pathogenic": 2,
            "VUS": 4,
            "Benign": 0,
            "Likely_benign": 0,
            "Conflicting": 0,
            "Other": 1,
        },
        "top_genes": [
            {"gene": "NGLY1", "variant_count": 10},
            {"gene": "ENG", "variant_count": 2},
        ],
    },
    "publications": {
        "total": 50,
        "by_year": {"2024": 10, "2023": 15, "2022": 12, "2021": 8},
        "recent": [{"pmid": "123", "title": "A study", "year": 2024, "url": "https://pubmed.ncbi.nlm.nih.gov/123/"}],
    },
    "source_status": {"orphanet": "ok", "clinvar": "ok", "pubmed": "ok", "omim": "skipped"},
    "errors": [],
}

# Fixture B: no genes, orphanet skipped
FIXTURE_NO_GENES = {
    "disease_id": "id-brugada",
    "canonical_name": "Brugada syndrome",
    "ids": {"orpha": None, "omim": "601144"},
    "synonyms": [],
    "genes": [],
    "phenotype_terms": [{"term": "arrhythmia", "count": 5, "source": "text"}],
    "pathway_terms": [],
    "clinvar": {"by_significance": {}, "top_genes": []},
    "publications": {"total": 0, "by_year": {}, "recent": []},
    "source_status": {"orphanet": "skipped", "clinvar": "skipped", "pubmed": "skipped", "omim": "ok"},
    "errors": [],
}


class TestCompactDiseaseSummary(unittest.TestCase):
    def test_gene_first_output_shape(self) -> None:
        out = compact_disease_summary(FIXTURE_GENE_FIRST)
        self.assertEqual(out["disease_id"], "id-ngly1")
        self.assertEqual(out["canonical_name"], "NGLY1 deficiency")
        self.assertEqual(out["ids"], {"orpha": "404454", "omim": "615273"})
        self.assertEqual(out["version"], "disease_short_v1")

    def test_genes_max_ten(self) -> None:
        out = compact_disease_summary(FIXTURE_GENE_FIRST)
        self.assertLessEqual(len(out["genes"]), 10)
        self.assertIn("NGLY1", out["genes"])
        self.assertIn("ENG", out["genes"])

    def test_phenotypes_top_max_twelve_filter_generic(self) -> None:
        out = compact_disease_summary(FIXTURE_GENE_FIRST)
        self.assertLessEqual(len(out["phenotypes_top"]), 12)
        terms = [p["term"] for p in out["phenotypes_top"]]
        self.assertIn("hypotonia", terms)
        self.assertIn("seizures", terms)
        self.assertNotIn("disease", terms)

    def test_pathways_top_max_twelve_filter_generic(self) -> None:
        out = compact_disease_summary(FIXTURE_GENE_FIRST)
        self.assertLessEqual(len(out["pathways_top"]), 12)
        terms = [p["term"] for p in out["pathways_top"]]
        self.assertIn("ubiquitin", terms)
        self.assertNotIn("pathway", terms)

    def test_clinvar_top_all_bins_max_eight_genes(self) -> None:
        out = compact_disease_summary(FIXTURE_GENE_FIRST)
        cs = out["clinvar_top"]
        for b in ["Pathogenic", "Likely_pathogenic", "VUS", "Benign", "Likely_benign", "Conflicting", "Other"]:
            self.assertIn(b, cs["by_significance"])
        self.assertLessEqual(len(cs["top_genes"]), 8)
        self.assertEqual(cs["top_genes"][0]["gene"], "NGLY1")
        self.assertEqual(cs["top_genes"][0]["variant_count"], 10)

    def test_stats_pubs_recent_years(self) -> None:
        out = compact_disease_summary(FIXTURE_GENE_FIRST)
        self.assertEqual(out["stats"]["pubs_total"], 50)
        self.assertLessEqual(len(out["stats"]["pubs_recent_years"]), 4)

    def test_no_genes_orphanet_skipped_notes(self) -> None:
        out = compact_disease_summary(FIXTURE_NO_GENES)
        self.assertEqual(out["genes"], [])
        self.assertIn("No genes detected", out["notes"])
        self.assertIn("No Orphanet data", out["notes"])
        self.assertIn("ClinVar unavailable", out["notes"])
        self.assertLessEqual(len(out["notes"]), 6)

    def test_caps_many_genes(self) -> None:
        raw = {
            **FIXTURE_GENE_FIRST,
            "genes": [{"symbol": f"G{i}", "source": "text"} for i in range(20)],
        }
        out = compact_disease_summary(raw)
        self.assertEqual(len(out["genes"]), 10)

    def test_deterministic(self) -> None:
        out1 = compact_disease_summary(FIXTURE_GENE_FIRST)
        out2 = compact_disease_summary(FIXTURE_GENE_FIRST)
        self.assertEqual(out1, out2)

    def test_empty_minimal_no_crash(self) -> None:
        out = compact_disease_summary({"disease_id": "x", "canonical_name": "y"})
        self.assertEqual(out["disease_id"], "x")
        self.assertEqual(out["canonical_name"], "y")
        self.assertEqual(out["genes"], [])
        self.assertEqual(out["version"], "disease_short_v1")


if __name__ == "__main__":
    unittest.main()
