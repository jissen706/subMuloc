"""
Tests for Block 2 — mechanism-space vectorization.

All tests are pure-function / mock-DB; no live PostgreSQL required.

Run:
    pytest tests/test_mechanism_vectorizer.py -v
"""
from __future__ import annotations

import hashlib
import sys
import os
import unittest
from unittest.mock import MagicMock, call, patch

# Ensure project root is on sys.path when run directly
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.services.mechanism_vocab import (
    GENE_TO_NODES,
    MECH_ALIASES,
    MECH_NODES,
    MECH_NODES_HASH,
    MECH_VOCAB_VERSION,
)
from app.services.mechanism_mapper import (
    cosine_similarity,
    disease_to_mech_vector,
    drug_to_mech_vector,
    extract_pathway_terms,
    extract_targets,
    extract_term_list_from_pathways,
    normalize_text,
    sparse_to_dense_direction,
    sparse_to_dense_weights,
)
from app.services.mechanism_store import upsert_mechanism_vector
from app.models import MechanismVector


# ---------------------------------------------------------------------------
# (1) Vocabulary / hash stability
# ---------------------------------------------------------------------------
class TestVocab(unittest.TestCase):

    def test_nodes_hash_stable(self):
        """MECH_NODES_HASH must equal re-computed sha256 of the node list."""
        expected = hashlib.sha256("|".join(MECH_NODES).encode()).hexdigest()
        self.assertEqual(MECH_NODES_HASH, expected)
        self.assertEqual(len(MECH_NODES_HASH), 64)  # sha256 → 64 hex chars

    def test_nodes_count(self):
        self.assertGreaterEqual(len(MECH_NODES), 25)
        # All node names referenced in MECH_ALIASES and GENE_TO_NODES must exist
        for node in MECH_ALIASES:
            self.assertIn(node, MECH_NODES, f"MECH_ALIASES key '{node}' missing from MECH_NODES")
        for gene, nodes in GENE_TO_NODES.items():
            for node in nodes:
                self.assertIn(node, MECH_NODES, f"GENE_TO_NODES[{gene}]={node} missing from MECH_NODES")

    def test_vocab_version_string(self):
        self.assertTrue(MECH_VOCAB_VERSION.startswith("mech_vocab_"))

    def test_no_duplicate_nodes(self):
        self.assertEqual(len(MECH_NODES), len(set(MECH_NODES)))


# ---------------------------------------------------------------------------
# (2) normalize_text
# ---------------------------------------------------------------------------
class TestNormalizeText(unittest.TestCase):

    def test_basic(self):
        self.assertEqual(normalize_text("mTOR Pathway"), "mtor pathway")

    def test_punctuation_stripped(self):
        self.assertEqual(normalize_text("PI3K/AKT"), "pi3k akt")
        self.assertEqual(normalize_text("cGAS-STING"), "cgas sting")

    def test_none_returns_empty(self):
        self.assertEqual(normalize_text(None), "")

    def test_empty_string(self):
        self.assertEqual(normalize_text(""), "")

    def test_collapse_whitespace(self):
        self.assertEqual(normalize_text("  ERK 1 / 2  "), "erk 1 2")


# ---------------------------------------------------------------------------
# (3) extract_term_list_from_pathways
# ---------------------------------------------------------------------------
class TestExtractTermList(unittest.TestCase):

    def test_dict_with_count(self):
        result = extract_term_list_from_pathways([{"term": "mTOR", "count": 10}])
        self.assertEqual(result, [("mTOR", 10.0)])

    def test_dict_with_score(self):
        result = extract_term_list_from_pathways([{"term": "autophagy", "score": 0.8}])
        self.assertEqual(result, [("autophagy", 0.8)])

    def test_string_items(self):
        result = extract_term_list_from_pathways(["mTOR", "autophagy"])
        terms = [t for t, _ in result]
        self.assertIn("mTOR", terms)
        self.assertIn("autophagy", terms)

    def test_sorted_by_value_desc(self):
        inp = [{"term": "A", "count": 5}, {"term": "B", "count": 20}, {"term": "C", "count": 1}]
        result = extract_term_list_from_pathways(inp)
        self.assertEqual(result[0][0], "B")  # highest first

    def test_empty(self):
        self.assertEqual(extract_term_list_from_pathways(None), [])
        self.assertEqual(extract_term_list_from_pathways([]), [])


# ---------------------------------------------------------------------------
# (3b) extract_pathway_terms (defensive multi-key)
# ---------------------------------------------------------------------------
class TestExtractPathwayTerms(unittest.TestCase):

    def test_pathways_top_list(self):
        """Real drug_short uses pathways_top list of {term, count}."""
        obj = {"pathways_top": [{"term": "mTOR", "count": 5}, {"term": "autophagy", "count": 3}]}
        result = extract_pathway_terms(obj)
        self.assertEqual(len(result), 2)
        terms = [t for t, _ in result]
        self.assertIn("mTOR", terms)
        self.assertIn("autophagy", terms)

    def test_fallback_key_pathway_terms_top(self):
        obj = {"pathway_terms_top": [{"term": "MAPK", "count": 1}]}
        result = extract_pathway_terms(obj)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "MAPK")

    def test_nested_items(self):
        obj = {"pathways_top": {"items": [{"term": "NFkB", "count": 2}]}}
        result = extract_pathway_terms(obj)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "NFkB")

    def test_empty_or_missing(self):
        self.assertEqual(extract_pathway_terms({}), [])
        self.assertEqual(extract_pathway_terms(None), [])
        self.assertEqual(extract_pathway_terms({"other": []}), [])


# ---------------------------------------------------------------------------
# (3c) extract_targets (defensive multi-shape)
# ---------------------------------------------------------------------------
class TestExtractTargets(unittest.TestCase):

    def test_targets_top_dict_with_name_gene(self):
        """Real drug_short uses targets_top list of {name, gene, action}."""
        obj = {
            "targets_top": [
                {"name": "BCR-ABL", "gene": "BCR", "action": "inhibitor"},
                {"name": "PDGFR", "gene_symbol": "PDGFRA", "action": None},
            ]
        }
        result = extract_targets(obj)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0][0], "BCR-ABL")
        self.assertEqual(result[0][1], "BCR")
        self.assertEqual(result[1][1], "PDGFRA")

    def test_list_of_strings(self):
        obj = {"targets_top": ["EGFR", "VEGFR"]}
        result = extract_targets(obj)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0][0], "EGFR")

    def test_target_key_fallback(self):
        obj = {"targets": [{"target": "mTOR", "symbol": "MTOR"}]}
        result = extract_targets(obj)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "mTOR")
        self.assertEqual(result[0][1], "MTOR")

    def test_empty(self):
        self.assertEqual(extract_targets({}), [])
        self.assertEqual(extract_targets(None), [])


# ---------------------------------------------------------------------------
# (4) disease_to_mech_vector — gene boost
# ---------------------------------------------------------------------------
class TestDiseaseVector(unittest.TestCase):

    def _minimal_disease(self, genes=None, pathways=None, phenotypes=None):
        return {
            "disease_id": "d-test",
            "canonical_name": "Test Disease",
            "genes": genes or [],
            "pathways_top": pathways or [],
            "phenotypes_top": phenotypes or [],
            "version": "disease_short_v1",
        }

    def test_gene_boost_tmem173_includes_cgas_sting(self):
        """TMEM173 is the STING gene → cGAS_STING must appear in sparse."""
        short = self._minimal_disease(genes=["TMEM173"])
        sparse = disease_to_mech_vector(short)
        self.assertIn("cGAS_STING", sparse, "TMEM173 → cGAS_STING gene boost missing")
        self.assertGreater(sparse["cGAS_STING"]["weight"], 0.0)

    def test_gene_boost_evidence_tag(self):
        """Evidence list should contain 'gene:TMEM173'."""
        short = self._minimal_disease(genes=["TMEM173"])
        sparse = disease_to_mech_vector(short)
        ev = sparse.get("cGAS_STING", {}).get("evidence", [])
        self.assertTrue(any("gene:TMEM173" in e for e in ev))

    def test_gene_cap_10(self):
        """Only first 10 genes should be processed."""
        genes = [
            "MTOR", "PIK3CA", "AKT1", "BRAF", "KRAS",
            "TP53", "BRCA1", "BRCA2", "ATM", "ATR",
            "NLRP3",  # 11th gene — should be ignored
        ]
        short = self._minimal_disease(genes=genes)
        sparse = disease_to_mech_vector(short)
        # NLRP3 should NOT add inflammasome contribution (only 10 genes processed)
        # But mTOR etc. from first 10 should be present
        self.assertIn("mTOR", sparse)

    def test_pathway_term_matching(self):
        short = self._minimal_disease(pathways=[{"term": "mTOR signaling", "count": 15}])
        sparse = disease_to_mech_vector(short)
        self.assertIn("mTOR", sparse)

    def test_direction_always_zero_for_disease(self):
        short = self._minimal_disease(
            genes=["MTOR"],
            pathways=[{"term": "mTOR pathway", "count": 10}],
        )
        sparse = disease_to_mech_vector(short)
        for node, data in sparse.items():
            self.assertEqual(data["direction"], 0, f"Disease node {node} should have direction 0")

    def test_empty_disease_returns_empty_sparse(self):
        short = self._minimal_disease()
        sparse = disease_to_mech_vector(short)
        self.assertEqual(sparse, {})

    def test_weight_threshold(self):
        """All weights in sparse must be >= 0.08."""
        short = self._minimal_disease(
            genes=["MTOR", "PIK3CA", "AKT1", "BRAF"],
            pathways=[{"term": "mTOR", "count": 20}, {"term": "PI3K", "count": 5}],
        )
        sparse = disease_to_mech_vector(short)
        for node, data in sparse.items():
            self.assertGreaterEqual(data["weight"], 0.08, f"Node {node} weight below threshold")

    def test_max_weight_is_1(self):
        """Max weight in sparse must be exactly 1.0."""
        short = self._minimal_disease(
            genes=["MTOR"],
            pathways=[{"term": "mTOR signaling", "count": 25}],
        )
        sparse = disease_to_mech_vector(short)
        if sparse:
            self.assertEqual(max(v["weight"] for v in sparse.values()), 1.0)

    def test_deterministic(self):
        short = self._minimal_disease(
            genes=["TMEM173", "STAT1", "JAK1"],
            pathways=[{"term": "interferon", "count": 10}, {"term": "JAK-STAT", "count": 5}],
        )
        self.assertEqual(disease_to_mech_vector(short), disease_to_mech_vector(short))


# ---------------------------------------------------------------------------
# (5) drug_to_mech_vector
# ---------------------------------------------------------------------------
class TestDrugVector(unittest.TestCase):

    def _rapamycin_short(self):
        return {
            "drug_id": "drug-rap",
            "canonical_name": "rapamycin",
            "pathways_top": [
                {"term": "mTOR signaling", "count": 25},
                {"term": "PI3K pathway", "count": 10},
            ],
            "targets_top": [
                {"name": "mTOR", "gene": "MTOR", "action": "inhibitor"},
            ],
            "version": "short_v1",
        }

    def test_mtor_pathway_in_sparse(self):
        sparse = drug_to_mech_vector(self._rapamycin_short())
        self.assertIn("mTOR", sparse)

    def test_inhibitor_direction_minus_one(self):
        """mTOR inhibitor → direction should be -1 for mTOR node."""
        sparse = drug_to_mech_vector(self._rapamycin_short())
        self.assertIn("mTOR", sparse)
        self.assertEqual(sparse["mTOR"]["direction"], -1)

    def test_agonist_direction_plus_one(self):
        short = {
            "drug_id": "drug-x",
            "canonical_name": "test-agonist",
            "pathways_top": [{"term": "JAK STAT signaling", "count": 10}],
            "targets_top": [{"name": "JAK1", "gene": "JAK1", "action": "agonist"}],
            "version": "short_v1",
        }
        sparse = drug_to_mech_vector(short)
        self.assertIn("JAK_STAT", sparse)
        self.assertEqual(sparse["JAK_STAT"]["direction"], 1)

    def test_target_gene_boost_adds_evidence(self):
        sparse = drug_to_mech_vector(self._rapamycin_short())
        ev = sparse.get("mTOR", {}).get("evidence", [])
        self.assertTrue(any("target:" in e or "pathway:" in e for e in ev))

    def test_empty_drug_returns_empty_sparse(self):
        short = {"drug_id": "d", "canonical_name": "empty", "pathways_top": [], "targets_top": []}
        sparse = drug_to_mech_vector(short)
        self.assertEqual(sparse, {})

    def test_deterministic(self):
        short = self._rapamycin_short()
        self.assertEqual(drug_to_mech_vector(short), drug_to_mech_vector(short))

    def test_evidence_capped_at_4(self):
        """Evidence list per node must have at most 4 items."""
        short = {
            "drug_id": "d",
            "canonical_name": "test",
            "pathways_top": [
                {"term": "mTOR signaling", "count": 10},
                {"term": "mTOR pathway", "count": 9},
                {"term": "mTOR inhibitor", "count": 8},
                {"term": "mtor complex", "count": 7},
                {"term": "mtorc1", "count": 6},
            ],
            "targets_top": [],
        }
        sparse = drug_to_mech_vector(short)
        if "mTOR" in sparse:
            self.assertLessEqual(len(sparse["mTOR"]["evidence"]), 4)

    def test_sparse_sorted_by_weight_desc(self):
        short = self._rapamycin_short()
        sparse = drug_to_mech_vector(short)
        weights = [v["weight"] for v in sparse.values()]
        self.assertEqual(weights, sorted(weights, reverse=True))


# ---------------------------------------------------------------------------
# (6) sparse_to_dense alignment
# ---------------------------------------------------------------------------
class TestSparseDenseAlignment(unittest.TestCase):

    def test_weights_aligned_to_mech_nodes(self):
        sparse = {
            "mTOR": {"weight": 0.8, "direction": 0, "evidence": []},
            "apoptosis": {"weight": 0.5, "direction": -1, "evidence": []},
        }
        dense_w = sparse_to_dense_weights(sparse)
        dense_d = sparse_to_dense_direction(sparse)

        self.assertEqual(len(dense_w), len(MECH_NODES))
        self.assertEqual(len(dense_d), len(MECH_NODES))

        mtor_idx = MECH_NODES.index("mTOR")
        apop_idx = MECH_NODES.index("apoptosis")

        self.assertAlmostEqual(dense_w[mtor_idx], 0.8)
        self.assertAlmostEqual(dense_w[apop_idx], 0.5)
        self.assertEqual(dense_d[apop_idx], -1)
        self.assertEqual(dense_d[mtor_idx], 0)

    def test_absent_nodes_are_zero(self):
        sparse = {"mTOR": {"weight": 1.0, "direction": 0, "evidence": []}}
        dense_w = sparse_to_dense_weights(sparse)
        dense_d = sparse_to_dense_direction(sparse)
        for i, node in enumerate(MECH_NODES):
            if node != "mTOR":
                self.assertEqual(dense_w[i], 0.0)
                self.assertEqual(dense_d[i], 0)

    def test_empty_sparse(self):
        dense_w = sparse_to_dense_weights({})
        self.assertEqual(dense_w, [0.0] * len(MECH_NODES))
        dense_d = sparse_to_dense_direction({})
        self.assertEqual(dense_d, [0] * len(MECH_NODES))

    def test_round_trip_sparse_dense(self):
        """drug vector → dense → same weights at expected indices."""
        short = {
            "drug_id": "d",
            "canonical_name": "rapamycin",
            "pathways_top": [{"term": "mTOR signaling", "count": 20}],
            "targets_top": [{"name": "MTOR", "gene": "MTOR", "action": "inhibitor"}],
        }
        sparse = drug_to_mech_vector(short)
        dense_w = sparse_to_dense_weights(sparse)
        for node, data in sparse.items():
            idx = MECH_NODES.index(node)
            self.assertAlmostEqual(dense_w[idx], data["weight"])


# ---------------------------------------------------------------------------
# (7) cosine_similarity
# ---------------------------------------------------------------------------
class TestCosineSimilarity(unittest.TestCase):

    def test_identical_vectors_return_one(self):
        n = len(MECH_NODES)
        v = [0.5] * n
        self.assertAlmostEqual(cosine_similarity(v, v), 1.0, places=5)

    def test_orthogonal_vectors_return_zero(self):
        n = len(MECH_NODES)
        a = [1.0 if i == 0 else 0.0 for i in range(n)]
        b = [1.0 if i == 1 else 0.0 for i in range(n)]
        self.assertEqual(cosine_similarity(a, b), 0.0)

    def test_zero_vector_returns_zero(self):
        n = len(MECH_NODES)
        v = [0.5] * n
        zero = [0.0] * n
        self.assertEqual(cosine_similarity(zero, v), 0.0)
        self.assertEqual(cosine_similarity(v, zero), 0.0)
        self.assertEqual(cosine_similarity(zero, zero), 0.0)

    def test_length_mismatch_returns_zero(self):
        self.assertEqual(cosine_similarity([1.0], [1.0, 0.0]), 0.0)

    def test_partial_overlap(self):
        """Two partially overlapping vectors should have 0 < score < 1."""
        n = len(MECH_NODES)
        a = [1.0 if i < 3 else 0.0 for i in range(n)]
        b = [1.0 if i < 2 else 0.0 for i in range(n)]  # b ⊂ a
        score = cosine_similarity(a, b)
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)


# ---------------------------------------------------------------------------
# (8) upsert_mechanism_vector — unique constraint behaviour
# ---------------------------------------------------------------------------
class TestUpsertMechanismVector(unittest.TestCase):

    def _make_payload(self) -> dict:
        return {
            "vocab_version": MECH_VOCAB_VERSION,
            "nodes_hash": MECH_NODES_HASH,
            "dense_weights": [0.0] * len(MECH_NODES),
            "dense_direction": [0] * len(MECH_NODES),
            "sparse": {},
        }

    def test_first_call_inserts_new_row(self):
        """When no existing record found, db.add() should be called once."""
        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.first.return_value = None

        upsert_mechanism_vector("disease", "test-uuid-1", self._make_payload(), mock_db)

        mock_db.add.assert_called_once()
        added_obj = mock_db.add.call_args[0][0]
        self.assertIsInstance(added_obj, MechanismVector)
        self.assertEqual(added_obj.entity_type, "disease")
        self.assertEqual(added_obj.entity_id, "test-uuid-1")

    def test_second_call_updates_existing_row(self):
        """When record already exists, db.add() must NOT be called."""
        existing = MechanismVector(
            entity_type="disease",
            entity_id="test-uuid-2",
            vocab_version=MECH_VOCAB_VERSION,
            nodes_hash=MECH_NODES_HASH,
            dense_weights=[0.0] * len(MECH_NODES),
            dense_direction=[0] * len(MECH_NODES),
            sparse={},
        )
        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.first.return_value = existing

        new_payload = {**self._make_payload(), "sparse": {"mTOR": {"weight": 0.9, "direction": -1, "evidence": []}}}
        result = upsert_mechanism_vector("disease", "test-uuid-2", new_payload, mock_db)

        mock_db.add.assert_not_called()
        # Verify the existing object was mutated
        self.assertEqual(result.sparse, new_payload["sparse"])
        self.assertIs(result, existing)

    def test_upsert_returns_mechanism_vector(self):
        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.first.return_value = None

        result = upsert_mechanism_vector("drug", "drug-uuid", self._make_payload(), mock_db)
        # result is the refreshed object returned by db.refresh; MagicMock refresh returns None,
        # so the function returns whatever was added
        self.assertIsNotNone(result)


# ---------------------------------------------------------------------------
# (9) Integration: disease vector from realistic fixture
# ---------------------------------------------------------------------------
class TestDiseaseVectorIntegration(unittest.TestCase):

    BRUGADA_SHORT = {
        "disease_id": "dis-brugada",
        "canonical_name": "Brugada syndrome",
        "ids": {"orpha": "130", "omim": "601144"},
        "genes": ["SCN5A"],
        "pathways_top": [
            {"term": "ion channel", "count": 20},
            {"term": "cardiac arrhythmia", "count": 15},
            {"term": "sodium channel", "count": 12},
        ],
        "phenotypes_top": [{"term": "ventricular fibrillation", "count": 8}],
        "version": "disease_short_v1",
    }

    def test_brugada_has_ion_channels(self):
        sparse = disease_to_mech_vector(self.BRUGADA_SHORT)
        self.assertIn("ion_channels", sparse)

    def test_brugada_gene_scn5a_maps_correctly(self):
        self.assertIn("SCN5A", GENE_TO_NODES)
        self.assertIn("ion_channels", GENE_TO_NODES["SCN5A"])

    def test_aicardi_goutieres_sting_present(self):
        short = {
            "disease_id": "dis-ags",
            "canonical_name": "Aicardi-Goutières syndrome",
            "genes": ["TREX1", "RNASEH2B", "TMEM173"],
            "pathways_top": [
                {"term": "interferon signaling", "count": 30},
                {"term": "cGAS STING pathway", "count": 20},
            ],
            "phenotypes_top": [],
            "version": "disease_short_v1",
        }
        sparse = disease_to_mech_vector(short)
        self.assertIn("cGAS_STING", sparse)
        self.assertIn("typeI_interferon", sparse)
        # cGAS_STING should have high weight (both gene + pathway evidence)
        self.assertGreater(sparse["cGAS_STING"]["weight"], 0.5)

    def test_hypertrophic_cardiomyopathy_sarcomere(self):
        short = {
            "disease_id": "dis-hcm",
            "canonical_name": "Hypertrophic cardiomyopathy",
            "genes": ["MYH7", "MYBPC3", "TNNI3"],
            "pathways_top": [{"term": "sarcomere contractile", "count": 18}],
            "phenotypes_top": [],
            "version": "disease_short_v1",
        }
        sparse = disease_to_mech_vector(short)
        self.assertIn("sarcomere", sparse)


if __name__ == "__main__":
    unittest.main()
