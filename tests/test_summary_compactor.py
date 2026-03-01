"""
Tests for summary_compactor.compact_drug_summary (deterministic, caps enforced).
"""
from __future__ import annotations

import os
import sys
import unittest

if __name__ == "__main__":
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

from app.services.summary_compactor import compact_drug_summary

# Exact keys expected from GET /drug/{id}/summary_short and build_drug_short (regression)
EXPECTED_DRUG_SHORT_KEYS = frozenset({
    "drug_id", "canonical_name", "drug_type", "identifiers", "structure",
    "targets_top", "trials", "safety", "pathways_top", "stats", "notes", "version",
})


# Minimal raw summary: small molecule with SMILES
FIXTURE_SMALL_MOLECULE = {
    "drug_id": "id-sm",
    "canonical_name": "imatinib",
    "identifiers": [
        {"id_type": "chembl_id", "value": "CHEMBL941"},
        {"id_type": "pubchem_cid", "value": "5291"},
        {"id_type": "inchikey", "value": "RITAVMQDGSYQRR-UHFFFAOYSA-N"},
    ],
    "molecular_structure": {
        "smiles": "CC1=C(C=C(C=C1)NC(=O)C2=CC=C(C=C2)CN3CCN(CC3)C)NC4=NC=CC(=N4)C5=CC=CN=C5",
        "inchi": "InChI=1S/...",
        "molecular_formula": "C29H31N7O",
        "molecular_weight": 493.6,
    },
    "targets": [
        {"target_name": "BCR-ABL", "gene_symbol": "BCR", "source": "chembl", "evidence": {"action": "inhibitor"}},
        {"target_name": "PDGFR", "gene_symbol": "PDGFRA", "source": "chembl", "evidence": None},
    ],
    "trials": {
        "total": 3,
        "by_phase": {"PHASE3": 2, "PHASE2": 1},
        "by_status": {"COMPLETED": 2, "TERMINATED": 1},
        "trials": [
            {"nct_id": "NCT0001", "title": "Trial One", "phase": "PHASE3", "status": "TERMINATED", "start_date": "2020-01", "url": "https://a"},
            {"nct_id": "NCT0002", "title": "Trial Two", "phase": "PHASE3", "status": "COMPLETED", "start_date": "2019-06", "url": "https://b"},
            {"nct_id": "NCT0003", "title": "Trial Three", "phase": "PHASE2", "status": "COMPLETED", "start_date": None, "url": None},
        ],
    },
    "publications": {"total": 100, "by_year": {2024: 10, 2023: 20, 2022: 15, 2021: 12, 2020: 8}},
    "label_warnings": [
        {"section": "boxed_warning", "text": "Cardiac risk.", "url": "https://labels"},
        {"section": "contraindications", "text": "Hypersensitivity.", "url": None},
    ],
    "toxicity_metrics": [
        {"metric_type": "QT_prolongation", "value": "5", "units": "ms", "interpreted_flag": "concerning", "evidence_source": "ctgov", "evidence_ref": "NCT0001", "notes": "Prolonged QT."},
        {"metric_type": "ALT_elevation", "value": "2x", "units": "ULN", "interpreted_flag": "unknown", "evidence_source": "pubmed", "evidence_ref": None, "notes": None},
    ],
    "pathway_mentions": [
        {"pathway_term": "tyrosine kinase", "count": 50, "max_confidence": 0.9, "evidence_sources": ["chembl"]},
        {"pathway_term": "metabolism", "count": 30, "max_confidence": 0.5, "evidence_sources": ["pubmed"]},
        {"pathway_term": "apoptosis", "count": 20, "max_confidence": 0.6, "evidence_sources": ["pubmed"]},
    ],
}

# Biologic: no SMILES, label text indicates biologic
FIXTURE_BIOLOGIC = {
    "drug_id": "id-bio",
    "canonical_name": "some antibody",
    "identifiers": [{"id_type": "chembl_id", "value": "CHEMBL1234"}],
    "molecular_structure": None,
    "targets": [],
    "trials": {"total": 0, "by_phase": {}, "by_status": {}, "trials": []},
    "publications": {"total": 5, "by_year": {2023: 3, 2022: 2}},
    "label_warnings": [{"section": "warnings", "text": "This biologic may cause infusion reactions.", "url": None}],
    "toxicity_metrics": [],
    "pathway_mentions": [],
}


class TestCompactDrugSummary(unittest.TestCase):
    def test_small_molecule_has_drug_type_and_structure(self) -> None:
        out = compact_drug_summary(FIXTURE_SMALL_MOLECULE)
        self.assertEqual(out["drug_type"], "small_molecule")
        self.assertEqual(out["drug_id"], "id-sm")
        self.assertEqual(out["canonical_name"], "imatinib")
        self.assertIn("smiles", out["structure"])
        self.assertEqual(out["structure"].get("formula"), "C29H31N7O")
        self.assertEqual(out["structure"].get("mw"), 493.6)
        self.assertEqual(out["version"], "short_v1")

    def test_small_molecule_identifiers(self) -> None:
        out = compact_drug_summary(FIXTURE_SMALL_MOLECULE)
        self.assertEqual(out["identifiers"].get("chembl_id"), "CHEMBL941")
        self.assertEqual(out["identifiers"].get("pubchem_cid"), "5291")
        self.assertEqual(out["identifiers"].get("inchikey"), "RITAVMQDGSYQRR-UHFFFAOYSA-N")

    def test_biologic_drug_type(self) -> None:
        out = compact_drug_summary(FIXTURE_BIOLOGIC)
        self.assertEqual(out["drug_type"], "biologic")
        self.assertEqual(out["structure"], {})
        self.assertIn("Biologic: no SMILES", out["notes"])

    def test_targets_top_max_five(self) -> None:
        out = compact_drug_summary(FIXTURE_SMALL_MOLECULE)
        self.assertLessEqual(len(out["targets_top"]), 5)
        self.assertEqual(len(out["targets_top"]), 2)
        self.assertEqual(out["targets_top"][0]["name"], "BCR-ABL")
        self.assertEqual(out["targets_top"][0]["gene"], "BCR")
        self.assertEqual(out["targets_top"][0]["action"], "inhibitor")

    def test_trials_notables_max_six_and_priority(self) -> None:
        out = compact_drug_summary(FIXTURE_SMALL_MOLECULE)
        notables = out["trials"]["notables"]
        self.assertLessEqual(len(notables), 6)
        self.assertEqual(out["trials"]["total"], 3)
        self.assertEqual(out["trials"]["phase_counts"], {"PHASE3": 2, "PHASE2": 1})
        # TERMINATED should appear first (safety-relevant)
        self.assertEqual(notables[0]["nct_id"], "NCT0001")
        self.assertEqual(notables[0]["status"], "TERMINATED")

    def test_pathways_top_max_twelve_and_filter_generic(self) -> None:
        out = compact_drug_summary(FIXTURE_SMALL_MOLECULE)
        terms = [p["term"] for p in out["pathways_top"]]
        self.assertLessEqual(len(out["pathways_top"]), 12)
        self.assertIn("tyrosine kinase", terms)
        self.assertNotIn("metabolism", terms)
        self.assertNotIn("apoptosis", terms)

    def test_safety_flags_max_eight_and_boxed_contra(self) -> None:
        out = compact_drug_summary(FIXTURE_SMALL_MOLECULE)
        self.assertTrue(out["safety"]["boxed_warning"])
        self.assertTrue(out["safety"]["contraindications_present"])
        self.assertLessEqual(len(out["safety"]["toxicity_flags"]), 8)
        self.assertEqual(len(out["safety"]["toxicity_flags"]), 2)
        # Concerning first
        self.assertEqual(out["safety"]["toxicity_flags"][0]["flag"], "concerning")

    def test_stats_and_notes(self) -> None:
        out = compact_drug_summary(FIXTURE_SMALL_MOLECULE)
        self.assertEqual(out["stats"]["pubs_total"], 100)
        self.assertIn(2024, out["stats"]["pubs_recent_years"])
        self.assertLessEqual(len(out["stats"]["pubs_recent_years"]), 4)
        self.assertIn("Has boxed warning", out["notes"])
        self.assertLessEqual(len(out["notes"]), 5)

    def test_caps_pathways_many(self) -> None:
        raw = {**FIXTURE_SMALL_MOLECULE, "pathway_mentions": [{"pathway_term": f"pathway_{i}", "count": 100 - i, "max_confidence": 0.5, "evidence_sources": []} for i in range(20)]}
        out = compact_drug_summary(raw)
        self.assertEqual(len(out["pathways_top"]), 12)

    def test_caps_toxicity_flags_many(self) -> None:
        raw = {
            **FIXTURE_SMALL_MOLECULE,
            "toxicity_metrics": [
                {"metric_type": f"T{i}", "value": "v", "units": "u", "interpreted_flag": "unknown", "evidence_source": "s", "evidence_ref": None, "notes": None}
                for i in range(15)
            ],
        }
        out = compact_drug_summary(raw)
        self.assertEqual(len(out["safety"]["toxicity_flags"]), 8)

    def test_deterministic_same_input_same_output(self) -> None:
        out1 = compact_drug_summary(FIXTURE_SMALL_MOLECULE)
        out2 = compact_drug_summary(FIXTURE_SMALL_MOLECULE)
        self.assertEqual(out1, out2)

    def test_empty_minimal_no_crash(self) -> None:
        minimal = {"drug_id": "x", "canonical_name": "y"}
        out = compact_drug_summary(minimal)
        self.assertEqual(out["drug_id"], "x")
        self.assertEqual(out["canonical_name"], "y")
        self.assertEqual(out["drug_type"], "unknown")
        self.assertEqual(out["identifiers"], {})
        self.assertEqual(out["structure"], {})
        self.assertEqual(out["targets_top"], [])
        self.assertEqual(out["trials"]["total"], 0)
        self.assertEqual(out["trials"]["notables"], [])
        self.assertFalse(out["safety"]["boxed_warning"])
        self.assertEqual(out["pathways_top"], [])
        self.assertEqual(out["version"], "short_v1")

    def test_drug_short_schema_keys_match_endpoint(self) -> None:
        """Regression: compact_drug_summary (and thus build_drug_short) output keys match GET /drug/{id}/summary_short."""
        out = compact_drug_summary(FIXTURE_SMALL_MOLECULE)
        self.assertEqual(set(out.keys()), EXPECTED_DRUG_SHORT_KEYS, "drug_short keys must match expected schema")


if __name__ == "__main__":
    unittest.main()
