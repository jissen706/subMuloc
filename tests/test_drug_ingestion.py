"""
Test drug ingestion (first scraping step) for a fixed set of drugs.

Run with the API's sync mode and real DB/Redis (no Celery). Either:

  # With pytest (install if needed: pip install pytest)
  INGEST_MODE=sync pytest tests/test_drug_ingestion.py -v

  # Or run this file directly (or hit Run in IDE)
  INGEST_MODE=sync python tests/test_drug_ingestion.py

Prerequisites: Postgres and Redis running, migrations applied.
E.g. start infra: docker compose up postgres redis -d
      then: DATABASE_URL=... REDIS_URL=... alembic upgrade head
"""
from __future__ import annotations

import os
import sys
import unittest

# Ensure project root is on path when running this file directly (e.g. IDE Run button)
if __name__ == "__main__":
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

# Force sync mode before app is loaded (get_settings is cached)
os.environ.setdefault("INGEST_MODE", "sync")

from fastapi.testclient import TestClient

from app.main import app


def _check_prerequisites() -> tuple[bool, str]:
    """Return (True, '') if DB and Redis are reachable, else (False, error_message)."""
    try:
        from sqlalchemy import text
        from app.config import get_settings
        from app.db import SessionLocal
        settings = get_settings()
        # DB
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
        except Exception as e:
            return False, f"Postgres not reachable: {e}. Start with: docker compose up postgres redis -d"
        finally:
            session.close()
        # Redis
        import redis
        r = redis.from_url(settings.redis_url, decode_responses=True)
        r.ping()
    except Exception as e:
        return False, f"Cannot connect (Postgres/Redis?): {e}. Start with: docker compose up postgres redis -d"
    return True, ""


# Drugs to test (first scraping step)
DRUGS = [
    "Imatinib",
    "Dasatinib",
    "Erlotinib",
    "Selumetinib",
    "Metformin",
]


class TestDrugIngestion(unittest.TestCase):
    """Test ingest + summary for each drug."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_health(self) -> None:
        """Health endpoint responds."""
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_ingest_imatinib(self) -> None:
        self._ingest_and_assert("Imatinib")

    def test_ingest_dasatinib(self) -> None:
        self._ingest_and_assert("Dasatinib")

    def test_ingest_erlotinib(self) -> None:
        self._ingest_and_assert("Erlotinib")

    def test_ingest_selumetinib(self) -> None:
        self._ingest_and_assert("Selumetinib")

    def test_ingest_metformin(self) -> None:
        self._ingest_and_assert("Metformin")

    def _ingest_and_assert(self, name: str) -> None:
        """POST /drug/ingest for name, then GET summary; assert minimal success."""
        # Ingest (sync mode: runs full pipeline in request)
        r = self.client.post(
            "/drug/ingest",
            json={"name": name},
            timeout=300,
        )
        self.assertEqual(r.status_code, 200, msg=f"ingest failed for {name}: {r.text}")
        body = r.json()
        self.assertIn("drug_id", body)
        self.assertIn("canonical_name", body)
        drug_id = body["drug_id"]
        canonical = body["canonical_name"]
        self.assertTrue(drug_id, msg=f"empty drug_id for {name}")
        self.assertTrue(canonical, msg=f"empty canonical_name for {name}")

        # In sync mode we get status completed and counts
        if body.get("status") == "completed":
            self.assertIn("counts", body)

        # Print per-test output so you can see results for each drug
        counts = body.get("counts") or {}
        print(
            f"\n  {name} -> drug_id={drug_id[:8]}... canonical_name={canonical!r} "
            f"| trials={counts.get('trials', '?')} pubs={counts.get('publications', '?')} "
            f"targets={counts.get('targets', '?')} warnings={counts.get('label_warnings', '?')} "
            f"tox={counts.get('toxicity_metrics', '?')} pathway={counts.get('pathway_mentions', '?')}"
        )

        # Summary must return 200 and the same drug
        r2 = self.client.get(f"/drug/{drug_id}/summary", timeout=30)
        self.assertEqual(r2.status_code, 200, msg=f"summary failed for {name}: {r2.text}")
        summary = r2.json()
        self.assertEqual(summary["drug_id"], drug_id)
        self.assertEqual(summary["canonical_name"], canonical)
        # Summary schema must be present (lists can be empty)
        self.assertIn("identifiers", summary)
        self.assertIn("trials", summary)
        self.assertIn("publications", summary)
        self.assertIn("targets", summary)


if __name__ == "__main__":
    ok, err = _check_prerequisites()
    if not ok:
        print(err, file=sys.stderr)
        sys.exit(1)
    unittest.main()
