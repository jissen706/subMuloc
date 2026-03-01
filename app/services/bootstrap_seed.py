"""
Bootstrap seed service: idempotent quickstart dataset loader.

Disabled by default. Enabled via ENABLE_BOOTSTRAP_ROUTES.
Uses existing ingestion, vectorization, and scoring logic.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Disease, DiseaseArtifact, Drug, MechanismVector
from app.services.disease_ingest import ingest_disease
from app.services.disease_resolver import resolve_disease_query
from app.services.disease_summary_compactor import compact_disease_summary
from app.services.drug_summary_builder import build_drug_short
from app.services.mechanism_mapper import (
    disease_to_mech_vector,
    drug_to_mech_vector,
    sparse_to_dense_direction,
    sparse_to_dense_weights,
)
from app.services.mechanism_store import upsert_mechanism_vector
from app.services.mechanism_vocab import MECH_NODES_HASH, MECH_VOCAB_VERSION
from app.services.node_tiering import compute_node_tiers

logger = logging.getLogger(__name__)


def build_why_summary(
    breakdown: dict,
    drug_short: dict,
    disease_short: dict,
    drug_sparse: dict,
    top_nodes: list,
) -> list[str]:
    """
    Deterministic why_summary from breakdown + tiers. No LLM.
    """
    lines: list[str] = []
    mech = breakdown.get("mechanism") or {}
    direction = breakdown.get("direction") or {}
    safety = breakdown.get("safety") or {}
    evidence = breakdown.get("evidence") or {}

    node_tiers = compute_node_tiers(drug_short or {}, drug_sparse or {})
    top_raw = top_nodes or mech.get("top_nodes") or []
    mech_score_val = round(float(mech.get("score") or 0), 3)
    lines.append(f"Mechanism: score={mech_score_val:.3f}, {len(top_raw)} shared nodes")
    for n in top_raw[:3]:
        if not isinstance(n, dict):
            continue
        node = (n.get("node") or "").strip()
        if not node:
            continue
        tier_info = node_tiers.get(node, {})
        tier = tier_info.get("tier", 0)
        support = tier_info.get("support", [])
        support_str = "+".join(support[:3]) if support else "pathway_text"
        lines.append(f"Top overlap: {node} (tier {tier}: {support_str})")

    node_effects = direction.get("node_effects") or []
    for eff in node_effects[:2]:
        if not isinstance(eff, dict):
            continue
        node = eff.get("node") or ""
        drug_dir = int(eff.get("drug_dir", 0))
        disease_dir = int(eff.get("disease_dir", 0))
        effect = float(eff.get("effect", 0))
        if node and effect > 0:
            lines.append(f"Direction: supportive (disease {disease_dir:+d}, drug {drug_dir:+d}) on {node}")
        elif node and effect < 0:
            lines.append(f"Direction: conflicting (disease {disease_dir:+d}, drug {drug_dir:+d}) on {node}")

    drug_stats = (drug_short or {}).get("stats") or {}
    disease_stats = (disease_short or {}).get("stats") or {}
    drug_pubs = int(drug_stats.get("pubs_total") or 0)
    disease_pubs = int(disease_stats.get("pubs_total") or 0)
    trials_total = 0
    trials_obj = (drug_short or {}).get("trials") or {}
    if isinstance(trials_obj, dict):
        trials_total = int(trials_obj.get("total") or 0)
    lines.append(f"Evidence: {trials_total} trials, {drug_pubs} drug pubs, {disease_pubs} disease pubs")

    boxed = False
    safety_obj = (drug_short or {}).get("safety") or {}
    if isinstance(safety_obj, dict):
        boxed = bool(safety_obj.get("boxed_warning"))
    penalty = float(safety.get("penalty") or 0)
    lines.append(f"Safety: boxed_warning={str(boxed).lower()}, penalty={penalty:.2f}")

    return lines[:8]

ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_DRUGS = ["Ruxolitinib", "Rapamycin", "Eculizumab"]
DEFAULT_DISEASES = [
    "STING-associated vasculopathy",
    "Aicardi-Goutières syndrome",
    "STAT1 gain-of-function disease",
    "Brugada syndrome",
    "Tuberous sclerosis complex",
    "Milroy disease",
    "Bardet-Biedl syndrome",
    "Joubert syndrome",
    "Familial hypercholesterolemia",
    "Pulmonary arterial hypertension",
]
DEFAULT_TOP_K = 10
DEFAULT_SKIP_EXISTING = True
DEFAULT_COMPARATORS_TOP_K = 10
DEFAULT_REQUIRE_MIN_DRUGS = 2
DEFAULT_REQUIRE_MIN_DISEASES = 6
DEFAULT_FORCE_VECTORIZE = False


def _parse_env_list(val: str | None) -> list[str]:
    """Parse comma-separated env var: strip, drop empty, dedupe preserving order."""
    if not val or not isinstance(val, str):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for part in val.split(","):
        s = part.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def load_bootstrap_config() -> dict[str, Any]:
    """
    Load bootstrap config. Priority: env lists > BOOTSTRAP_SEED_PATH file >
    artifacts/bootstrap_seed.json > example > internal defaults.
    """
    config_source = "defaults"
    drugs: list[str] = list(DEFAULT_DRUGS)
    diseases: list[str] = list(DEFAULT_DISEASES)
    top_k = DEFAULT_TOP_K
    skip_existing = DEFAULT_SKIP_EXISTING
    comparators_top_k = DEFAULT_COMPARATORS_TOP_K
    require_min_drugs = DEFAULT_REQUIRE_MIN_DRUGS
    require_min_diseases = DEFAULT_REQUIRE_MIN_DISEASES
    force_vectorize = DEFAULT_FORCE_VECTORIZE

    config_path = os.environ.get("BOOTSTRAP_SEED_PATH")
    if config_path is None:
        config_path = str(ROOT / "artifacts" / "bootstrap_seed.json")

    file_data: dict | None = None
    paths_to_try = [config_path]
    if not os.path.isfile(config_path):
        paths_to_try.append(str(ROOT / "artifacts" / "bootstrap_seed.example.json"))

    for path in paths_to_try:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    file_data = json.load(f)
                if file_data is not None and isinstance(file_data, dict):
                    config_source = "file"
                break
            except json.JSONDecodeError as e:
                print(f"Warning: malformed JSON in {path}: {e}. Using defaults.", file=sys.stderr)
                break
            except OSError as e:
                print(f"Warning: could not read {path}: {e}. Using defaults.", file=sys.stderr)
                break

    if file_data:
        if isinstance(file_data.get("drugs"), list):
            drugs = [str(x).strip() for x in file_data["drugs"] if str(x).strip()]
        if isinstance(file_data.get("diseases"), list):
            diseases = [str(x).strip() for x in file_data["diseases"] if str(x).strip()]
        if isinstance(file_data.get("top_k"), (int, float)):
            top_k = max(1, min(200, int(file_data["top_k"])))
        if "skip_existing" in file_data:
            skip_existing = bool(file_data["skip_existing"])
        if isinstance(file_data.get("comparators_top_k"), (int, float)):
            comparators_top_k = max(1, min(200, int(file_data["comparators_top_k"])))
        if isinstance(file_data.get("require_min_drugs"), (int, float)):
            require_min_drugs = max(0, int(file_data["require_min_drugs"]))
        if isinstance(file_data.get("require_min_diseases"), (int, float)):
            require_min_diseases = max(0, int(file_data["require_min_diseases"]))
        if "force_vectorize" in file_data:
            force_vectorize = bool(file_data["force_vectorize"])

    env_drugs = _parse_env_list(os.environ.get("BOOTSTRAP_DRUGS"))
    env_diseases = _parse_env_list(os.environ.get("BOOTSTRAP_DISEASES"))
    if env_drugs:
        drugs = env_drugs
        config_source = "env"
    if env_diseases:
        diseases = env_diseases
        config_source = "env"

    if diseases:
        top_k = min(top_k, len(diseases))

    return {
        "drugs": drugs,
        "diseases": diseases,
        "top_k": top_k,
        "skip_existing": skip_existing,
        "comparators_top_k": comparators_top_k,
        "require_min_drugs": require_min_drugs,
        "require_min_diseases": require_min_diseases,
        "force_vectorize": force_vectorize,
        "_config_source": config_source,
    }


def ensure_bootstrap_seed(
    db: Session,
    ingest_mode: str = "sync",
    poll_timeout_s: int = 180,
    force: bool = False,
) -> dict[str, Any]:
    """
    Idempotent bootstrap: seed drugs + diseases if missing, vectorize, return report.
    """
    cfg = load_bootstrap_config()
    config_source = cfg.pop("_config_source", "defaults")
    drugs = cfg["drugs"]
    diseases = cfg["diseases"]
    skip_existing = cfg["skip_existing"]
    force_vectorize = force or cfg["force_vectorize"]
    require_min_drugs = cfg["require_min_drugs"]
    require_min_diseases = cfg["require_min_diseases"]

    report: dict[str, Any] = {
        "ok": True,
        "config_source": config_source,
        "bootstrap_config_used": cfg,
        "drugs": [],
        "diseases": [],
        "vectorization": {"drugs_vectorized": 0, "diseases_vectorized": 0},
        "warnings": [],
        "errors": [],
    }

    drug_ids: list[str] = []
    disease_ids: list[str] = []

    # --- Drug ingestion ---
    from app.services import resolver
    from app.tasks.ingest import run_pipeline

    for name in drugs:
        try:
            existing = db.execute(
                select(Drug).where(Drug.canonical_name.ilike(name.strip()))
            ).scalar_one_or_none()
            if existing:
                drug_id = existing.id
                report["drugs"].append({"name": name, "drug_id": drug_id, "status": "skipped"})
                drug_ids.append(drug_id)
                continue

            ctx = resolver.resolve(db, name)
            ctx_dict = {
                "drug_id": ctx.drug_id,
                "canonical_name": ctx.canonical_name,
                "input_name": ctx.input_name,
                "synonyms": list(ctx.synonyms),
                "identifiers": ctx.identifiers,
            }

            if ingest_mode == "async":
                import time
                from app.tasks.ingest import celery_app, ingest_drug as celery_task
                task = celery_task.delay(ctx.drug_id, ctx_dict)
                t0 = time.time()
                while (time.time() - t0) < poll_timeout_s:
                    result = celery_app.AsyncResult(task.id)
                    if result.ready():
                        if result.successful():
                            report["drugs"].append({"name": name, "drug_id": ctx.drug_id, "status": "ok"})
                            drug_ids.append(ctx.drug_id)
                        else:
                            report["drugs"].append({"name": name, "drug_id": ctx.drug_id, "status": "error", "error": str(result.result)})
                            report["errors"].append(f"Drug {name}: async task failed")
                        break
                    time.sleep(2)
                else:
                    report["drugs"].append({"name": name, "drug_id": ctx.drug_id, "status": "error", "error": "timeout"})
                    report["errors"].append(f"Drug {name}: async timeout")
            else:
                run_pipeline(ctx.drug_id, ctx_dict)
                report["drugs"].append({"name": name, "drug_id": ctx.drug_id, "status": "ok"})
                drug_ids.append(ctx.drug_id)
        except Exception as e:
            logger.exception("bootstrap_drug_error name=%s", name)
            report["drugs"].append({"name": name, "drug_id": "", "status": "error", "error": str(e)})
            report["errors"].append(f"Drug {name}: {e}")

    # --- Disease ingestion ---
    for query in diseases:
        try:
            resolved = resolve_disease_query(query)
            canonical_name = resolved["canonical_name"]
            ids = resolved["ids"]
            ids_json = {"orpha": ids.get("orpha"), "omim": ids.get("omim")}

            existing = db.execute(
                select(Disease).where(Disease.canonical_name == canonical_name)
            ).scalar_one_or_none()
            if existing:
                disease_id = existing.id
                report["diseases"].append({"query": query, "disease_id": disease_id, "status": "skipped"})
                disease_ids.append(disease_id)
                continue

            disease = Disease(canonical_name=canonical_name, ids_json=ids_json)
            db.add(disease)
            db.flush()
            db.refresh(disease)

            raw_summary = ingest_disease(disease, query_hint=query)
            raw_summary["disease_id"] = disease.id
            raw_summary["canonical_name"] = disease.canonical_name

            artifact = DiseaseArtifact(
                disease_id=disease.id,
                kind="summary_raw",
                payload=raw_summary,
            )
            db.add(artifact)
            db.commit()

            report["diseases"].append({"query": query, "disease_id": disease.id, "status": "ok"})
            disease_ids.append(disease.id)
        except Exception as e:
            logger.exception("bootstrap_disease_error query=%s", query)
            db.rollback()
            report["diseases"].append({"query": query, "disease_id": "", "status": "error", "error": str(e)})
            report["errors"].append(f"Disease {query}: {e}")

    db.commit()

    # --- Vectorization ---
    drugs_vect = 0
    diseases_vect = 0

    for drug_id in drug_ids:
        if not drug_id:
            continue
        try:
            existing = db.execute(
                select(MechanismVector).where(
                    MechanismVector.entity_type == "drug",
                    MechanismVector.entity_id == drug_id,
                    MechanismVector.vocab_version == MECH_VOCAB_VERSION,
                    MechanismVector.nodes_hash == MECH_NODES_HASH,
                )
            ).first()
            if existing and not force_vectorize:
                drugs_vect += 1
                continue
            short = build_drug_short(drug_id, db)
            if short is None:
                report["warnings"].append(f"Drug {drug_id}: no short summary, skip vectorize")
                continue
            sparse = drug_to_mech_vector(short)
            payload = {
                "vocab_version": MECH_VOCAB_VERSION,
                "nodes_hash": MECH_NODES_HASH,
                "dense_weights": sparse_to_dense_weights(sparse),
                "dense_direction": sparse_to_dense_direction(sparse),
                "sparse": sparse,
            }
            upsert_mechanism_vector("drug", drug_id, payload, db)
            drugs_vect += 1
        except Exception as e:
            report["warnings"].append(f"Vectorize drug {drug_id}: {e}")

    for disease_id in disease_ids:
        if not disease_id:
            continue
        try:
            existing = db.execute(
                select(MechanismVector).where(
                    MechanismVector.entity_type == "disease",
                    MechanismVector.entity_id == disease_id,
                    MechanismVector.vocab_version == MECH_VOCAB_VERSION,
                    MechanismVector.nodes_hash == MECH_NODES_HASH,
                )
            ).first()
            if existing and not force_vectorize:
                diseases_vect += 1
                continue
            art = db.execute(
                select(DiseaseArtifact).where(
                    DiseaseArtifact.disease_id == disease_id,
                    DiseaseArtifact.kind == "summary_raw",
                )
            ).first()
            if not art or not art.payload:
                report["warnings"].append(f"Disease {disease_id}: no summary, skip vectorize")
                continue
            short = compact_disease_summary(art.payload or {})
            sparse = disease_to_mech_vector(short)
            payload = {
                "vocab_version": MECH_VOCAB_VERSION,
                "nodes_hash": MECH_NODES_HASH,
                "dense_weights": sparse_to_dense_weights(sparse),
                "dense_direction": sparse_to_dense_direction(sparse),
                "sparse": sparse,
            }
            upsert_mechanism_vector("disease", disease_id, payload, db)
            diseases_vect += 1
        except Exception as e:
            report["warnings"].append(f"Vectorize disease {disease_id}: {e}")

    report["vectorization"] = {"drugs_vectorized": drugs_vect, "diseases_vectorized": diseases_vect}

    ok_drugs = sum(1 for d in report["drugs"] if d.get("status") in ("ok", "skipped") and d.get("drug_id"))
    ok_diseases = sum(1 for d in report["diseases"] if d.get("status") in ("ok", "skipped") and d.get("disease_id"))

    if ok_drugs < require_min_drugs:
        report["ok"] = False
        report["errors"].append(f"Fewer than {require_min_drugs} drugs: {ok_drugs}")
    if ok_diseases < require_min_diseases:
        report["ok"] = False
        report["errors"].append(f"Fewer than {require_min_diseases} diseases: {ok_diseases}")

    db.commit()
    return report
