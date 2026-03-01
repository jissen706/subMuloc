#!/usr/bin/env python3
"""
Validate Block 2 (mechanism vectors) against real ingested data.

Run: python scripts/validate_block2.py

- Connects to DB via existing settings.
- Selects 3 drugs and 10 diseases with short summaries (or most recent).
- Runs internal vectorization (no HTTP), asserts dense_weights length == len(MECH_NODES).
- Asserts sparse non-empty for at least 2/3 drugs and 6/10 diseases.
- Runs cosine search drug->diseases and prints top 5 with overlap nodes.

If DB is empty, prints instructions to ingest a demo drug + disease first.
"""
from __future__ import annotations

import os
import sys

# Project root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

def main() -> None:
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import Drug, Disease, DiseaseArtifact, MechanismVector
    from app.services.drug_summary_builder import build_drug_short
    from app.services.disease_summary_compactor import compact_disease_summary
    from app.services.mechanism_mapper import (
        drug_to_mech_vector,
        disease_to_mech_vector,
        sparse_to_dense_weights,
        cosine_similarity,
    )
    from app.services.mechanism_vocab import MECH_NODES
    from sqlalchemy import select

    settings = get_settings()
    print(f"DB: {settings.database_url[:50]}...")
    session = SessionLocal()
    try:
        # Drugs with short summary (we need build_drug_short to return non-None)
        drug_ids: list[str] = []
        rows = session.execute(
            select(Drug.id).order_by(Drug.updated_at.desc()).limit(10)
        ).scalars().all()
        for did in rows:
            short = build_drug_short(did, session)
            if short and (short.get("pathways_top") or short.get("targets_top")):
                drug_ids.append(did)
            if len(drug_ids) >= 3:
                break
        if len(drug_ids) < 3:
            # Take any 3 most recent drugs
            for did in (rows or session.execute(select(Drug.id).limit(5)).scalars().all()):
                if did not in drug_ids:
                    drug_ids.append(did)
                if len(drug_ids) >= 3:
                    break

        # Diseases with summary_raw artifact
        disease_ids: list[str] = []
        art_rows = session.execute(
            select(DiseaseArtifact.disease_id).where(
                DiseaseArtifact.kind == "summary_raw"
            ).distinct()
        ).scalars().all()
        for did in art_rows[:10]:
            disease_ids.append(did)
        if not disease_ids:
            print(
                "No diseases with ingested summary found. Ingest at least one:\n"
                "  curl -X POST http://localhost:8000/disease/ingest -H 'Content-Type: application/json' -d '{\"query\": \"Brugada syndrome\"}'"
            )
        if not drug_ids:
            print(
                "No drugs found. Ingest at least one:\n"
                "  curl -X POST http://localhost:8000/drug/ingest -H 'Content-Type: application/json' -d '{\"name\": \"Metformin\"}'"
            )
        if not drug_ids or not disease_ids:
            session.close()
            sys.exit(1)

        n_drugs = min(3, len(drug_ids))
        n_diseases = min(10, len(disease_ids))
        drug_ids = drug_ids[:n_drugs]
        disease_ids = disease_ids[:n_diseases]

        print(f"\nValidating {n_drugs} drugs, {n_diseases} diseases\n")

        # Validate drugs
        drug_empty = 0
        for did in drug_ids:
            short = build_drug_short(did, session)
            if not short:
                print(f"  Drug {did}: no short summary")
                drug_empty += 1
                continue
            sparse = drug_to_mech_vector(short)
            dense = sparse_to_dense_weights(sparse)
            assert len(dense) == len(MECH_NODES), f"drug {did} dense len {len(dense)} != {len(MECH_NODES)}"
            top5 = sorted(
                (k for k, v in (sparse or {}).items() if v.get("weight", 0) > 0),
                key=lambda n: -(sparse.get(n, {}).get("weight", 0)),
            )[:5]
            pathway_field = "pathways_top" if short.get("pathways_top") else "(none)"
            pathway_count = len(short.get("pathways_top") or [])
            print(f"  Drug {did[:8]}... pathways_top={pathway_count} -> sparse nodes {len(sparse or {})} top5={top5}")
            if not sparse:
                drug_empty += 1
                print(f"    DEBUG: pathway field used: {pathway_field}, pathway terms: {pathway_count}")

        assert drug_empty <= 1, f"Too many drugs with empty sparse: {drug_empty}/{n_drugs} (allow at most 1)"

        # Validate diseases
        disease_empty = 0
        for did in disease_ids:
            art = session.execute(
                select(DiseaseArtifact).where(
                    DiseaseArtifact.disease_id == did,
                    DiseaseArtifact.kind == "summary_raw",
                )
            ).scalars().first()
            if not art or not art.payload:
                disease_empty += 1
                continue
            short = compact_disease_summary(art.payload or {})
            sparse = disease_to_mech_vector(short)
            dense = sparse_to_dense_weights(sparse)
            assert len(dense) == len(MECH_NODES), f"disease {did} dense len {len(dense)} != {len(MECH_NODES)}"
            genes = short.get("genes") or []
            top5 = sorted(
                (k for k, v in (sparse or {}).items() if v.get("weight", 0) > 0),
                key=lambda n: -(sparse.get(n, {}).get("weight", 0)),
            )[:5]
            print(f"  Disease {did[:8]}... genes={len(genes)} -> sparse nodes {len(sparse or {})} top5={top5}")
            if not sparse:
                disease_empty += 1
                print(f"    DEBUG: pathways_top count={len(short.get('pathways_top') or [])}, phenotypes_top={len(short.get('phenotypes_top') or [])}, gene_boosts applied={bool(genes)}")

        assert disease_empty <= 4, f"Too many diseases with empty sparse: {disease_empty}/{n_diseases} (allow at most 4)"

        # Cosine search: pick first drug, get or compute vectors for drug + all diseases
        drug_id = drug_ids[0]
        short_d = build_drug_short(drug_id, session)
        sparse_d = drug_to_mech_vector(short_d) if short_d else {}
        weights_d = sparse_to_dense_weights(sparse_d)

        if not any(w > 0 for w in weights_d):
            print("\nDrug has zero vector; skipping search.")
        else:
            scored = []
            for did in disease_ids:
                art = session.execute(
                    select(DiseaseArtifact).where(
                        DiseaseArtifact.disease_id == did,
                        DiseaseArtifact.kind == "summary_raw",
                    )
                ).scalars().first()
                if not art or not art.payload:
                    continue
                short = compact_disease_summary(art.payload or {})
                sparse = disease_to_mech_vector(short)
                weights = sparse_to_dense_weights(sparse)
                score = cosine_similarity(weights_d, weights)
                if score <= 0:
                    continue
                overlap_nodes = sorted(
                    [n for n in (sparse_d or {}) if n in (sparse or {}) and (sparse_d.get(n, {}).get("weight", 0) > 0 and (sparse or {}).get(n, {}).get("weight", 0) > 0)],
                    key=lambda n: -min(sparse_d.get(n, {}).get("weight", 0), (sparse or {}).get(n, {}).get("weight", 0)),
                )[:5]
                scored.append((did, score, overlap_nodes))
            scored.sort(key=lambda x: -x[1])
            print("\nTop 5 diseases_for_drug (cosine):")
            for did, score, nodes in scored[:5]:
                print(f"  {did[:8]}... score={score:.4f} overlap_nodes={nodes}")

        print("\nValidation OK.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
