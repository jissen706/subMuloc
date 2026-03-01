"""
Tests for bootstrap config loading. No DB/network.
"""
from __future__ import annotations

import os
import sys
import unittest

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)


class TestBootstrapConfig(unittest.TestCase):

    def setUp(self) -> None:
        for k in ("BOOTSTRAP_DRUGS", "BOOTSTRAP_DISEASES", "BOOTSTRAP_SEED_PATH"):
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k in ("BOOTSTRAP_DRUGS", "BOOTSTRAP_DISEASES", "BOOTSTRAP_SEED_PATH"):
            os.environ.pop(k, None)

    def test_config_priority_env_overrides_file(self) -> None:
        from app.services.bootstrap_seed import load_bootstrap_config
        os.environ["BOOTSTRAP_DRUGS"] = "DrugA,DrugB"
        os.environ["BOOTSTRAP_DISEASES"] = "Disease1,Disease2"
        cfg = load_bootstrap_config()
        self.assertEqual(cfg["drugs"], ["DrugA", "DrugB"])
        self.assertEqual(cfg["diseases"], ["Disease1", "Disease2"])
        self.assertEqual(cfg["_config_source"], "env")

    def test_config_dedupe_and_strip(self) -> None:
        from app.services.bootstrap_seed import load_bootstrap_config
        os.environ["BOOTSTRAP_DRUGS"] = "  A , B , A , C  "
        cfg = load_bootstrap_config()
        self.assertEqual(cfg["drugs"], ["A", "B", "C"])
        self.assertEqual(cfg["_config_source"], "env")

    def test_config_malformed_json_falls_back(self) -> None:
        import tempfile
        from app.services.bootstrap_seed import load_bootstrap_config
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ invalid json")
            path = f.name
        try:
            os.environ["BOOTSTRAP_SEED_PATH"] = path
            cfg = load_bootstrap_config()
            self.assertEqual(cfg["drugs"], ["Ruxolitinib", "Rapamycin", "Eculizumab"])
            self.assertEqual(cfg["_config_source"], "defaults")
        finally:
            os.unlink(path)
            os.environ.pop("BOOTSTRAP_SEED_PATH", None)

    def test_config_defaults(self) -> None:
        from app.services.bootstrap_seed import load_bootstrap_config
        cfg = load_bootstrap_config()
        self.assertIn("Ruxolitinib", cfg["drugs"])
        # bootstrap_seed.json contains the interferonopathy cluster; pick a stable entry
        self.assertIn("STING-associated vasculopathy", cfg["diseases"])
        self.assertGreaterEqual(cfg["require_min_drugs"], 0)
        self.assertGreaterEqual(cfg["require_min_diseases"], 0)
