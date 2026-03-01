"""
Deterministic compactor: raw drug summary dict -> short summary dict for UI + scoring.
Pure function: same input -> same output. No LLM calls.
"""
from __future__ import annotations

# Generic terms to exclude from pathways_top (task rule G)
PATHWAY_EXCLUDE_TERMS = frozenset({
    "metabolism", "apoptosis", "cytokine", "checkpoint", "interferon",
    "reactive oxygen species", "ROS", "innate immune",
})

# Safety-relevant trial statuses for notables priority (rule E)
TRIAL_SAFETY_STATUSES = frozenset({"TERMINATED", "SUSPENDED", "WITHDRAWN"})


def _trunc(s: str | None, max_len: int) -> str:
    if s is None:
        return ""
    s = s.strip()
    return s[:max_len] + ("..." if len(s) > max_len else "")


def _get_identifier(identifiers: list[dict], id_type: str) -> str | None:
    for i in identifiers or []:
        if (i.get("id_type") or "").lower() == id_type.lower():
            return i.get("value") or None
    return None


def compact_drug_summary(summary: dict) -> dict:
    """
    Transform raw summary (from DrugSummaryOut.model_dump()) into short format.
    Deterministic; handles missing/empty fields.
    """
    drug_id = summary.get("drug_id") or ""
    canonical_name = summary.get("canonical_name") or ""
    identifiers_list = summary.get("identifiers") or []
    mol = summary.get("molecular_structure") or {}
    targets_list = summary.get("targets") or []
    trials_obj = summary.get("trials") or {}
    trials_list = trials_obj.get("trials") or []
    pubs = summary.get("publications") or {}
    label_warnings = summary.get("label_warnings") or []
    toxicity_metrics = summary.get("toxicity_metrics") or []
    pathway_mentions = summary.get("pathway_mentions") or []

    # --- drug_type (rule A) ---
    smiles = mol.get("smiles") if isinstance(mol, dict) else None
    if smiles and str(smiles).strip():
        drug_type = "small_molecule"
    else:
        # Check for biologic indicator in label text or canonical name
        label_text = " ".join(
            (w.get("text") or "") for w in label_warnings if isinstance(w, dict)
        ).lower()
        name_lower = (canonical_name or "").lower()
        if "biologic" in label_text or "antibody" in label_text or "biologic" in name_lower or "antibody" in name_lower:
            drug_type = "biologic"
        else:
            drug_type = "unknown"

    # --- identifiers (rule B) ---
    identifiers: dict[str, str] = {}
    for key in ("chembl_id", "pubchem_cid", "inchikey", "cas"):
        val = _get_identifier(identifiers_list, key)
        if val is not None:
            identifiers[key] = val

    # --- structure (rule C) ---
    structure: dict = {}
    if mol.get("smiles"):
        structure["smiles"] = mol["smiles"]
    inchikey = mol.get("inchi")  # schema has inchi, not inchikey in structure; identifiers may have inchikey
    if not inchikey and identifiers.get("inchikey"):
        inchikey = identifiers["inchikey"]
    if inchikey:
        structure["inchikey"] = inchikey
    if mol.get("molecular_formula"):
        structure["formula"] = mol["molecular_formula"]
    if mol.get("molecular_weight") is not None:
        try:
            structure["mw"] = float(mol["molecular_weight"])
        except (TypeError, ValueError):
            pass

    # --- targets_top (rule D), max 5 ---
    targets_top: list[dict] = []
    for t in targets_list[:5]:
        if not isinstance(t, dict):
            continue
        action = None
        ev = t.get("evidence")
        if isinstance(ev, dict) and ev.get("action"):
            action = str(ev["action"])[:64]
        elif isinstance(ev, str):
            action = ev[:64]
        targets_top.append({
            "name": (t.get("target_name") or "").strip() or (t.get("gene_symbol") or "Unknown"),
            "gene": t.get("gene_symbol"),
            "action": action,
        })

    # --- trials (rule E) ---
    by_phase = trials_obj.get("by_phase") or {}
    by_status = trials_obj.get("by_status") or {}
    # Build notables: up to 6 with priority 1) safety status 2) phase 3/4 3) recent by start_date
    safety_relevant = []
    phase3_4 = []
    rest = []
    for t in trials_list:
        if not isinstance(t, dict):
            continue
        st = (t.get("status") or "").strip().upper()
        ph = (t.get("phase") or "").upper()
        if st in TRIAL_SAFETY_STATUSES:
            safety_relevant.append(t)
        elif "PHASE3" in ph or "PHASE4" in ph:
            phase3_4.append(t)
        else:
            rest.append(t)
    # Sort rest by start_date desc (None last)
    rest.sort(key=lambda x: (x.get("start_date") or "") or "0000", reverse=True)
    ordered = safety_relevant + phase3_4 + rest
    notables: list[dict] = []
    for t in ordered[:6]:
        notables.append({
            "nct_id": t.get("nct_id") or "",
            "phase": t.get("phase"),
            "status": t.get("status"),
            "title": _trunc(t.get("title"), 120),
            "url": t.get("url"),
        })
    trials_out = {
        "total": trials_obj.get("total") or 0,
        "phase_counts": dict(by_phase),
        "status_counts": dict(by_status),
        "notables": notables,
    }

    # --- safety (rule F) ---
    boxed_warning = any(
        (w.get("section") or "").strip().lower() == "boxed_warning"
        for w in label_warnings if isinstance(w, dict)
    )
    contraindications_present = any(
        (w.get("section") or "").strip().lower() == "contraindications"
        for w in label_warnings if isinstance(w, dict)
    )
    # Toxicity flags: up to 8, prioritize "concerning" first
    tox_concerning = [m for m in toxicity_metrics if isinstance(m, dict) and (m.get("interpreted_flag") or "").strip().lower() == "concerning"]
    tox_other = [m for m in toxicity_metrics if isinstance(m, dict) and m not in tox_concerning]
    toxicity_flags: list[dict] = []
    for m in (tox_concerning + tox_other)[:8]:
        toxicity_flags.append({
            "type": (m.get("metric_type") or "").strip() or "unknown",
            "flag": (m.get("interpreted_flag") or "").strip() or "unknown",
            "ref": m.get("evidence_ref"),
            "source": m.get("evidence_source"),
            "note": _trunc(m.get("notes"), 140),
        })
    faers_signal_count: int | None = None
    for m in toxicity_metrics:
        if not isinstance(m, dict):
            continue
        if (m.get("evidence_ref") or "").strip().lower() != "faers":
            continue
        u = (m.get("units") or "").lower()
        if "high_signal_events" in u:
            try:
                faers_signal_count = int(m.get("value") or 0)
            except (TypeError, ValueError):
                pass
            break
    safety = {
        "boxed_warning": boxed_warning,
        "contraindications_present": contraindications_present,
        "toxicity_flags": toxicity_flags,
    }
    if faers_signal_count is not None:
        safety["faers_signal_count"] = faers_signal_count

    # --- pathways_top (rule G), filter generic, max 12 ---
    pathway_sorted = sorted(
        (p for p in pathway_mentions if isinstance(p, dict)),
        key=lambda x: (-(x.get("count") or 0), x.get("pathway_term") or ""),
    )
    pathways_top: list[dict] = []
    for p in pathway_sorted:
        term = (p.get("pathway_term") or "").strip().lower()
        if term in PATHWAY_EXCLUDE_TERMS:
            continue
        pathways_top.append({
            "term": (p.get("pathway_term") or "").strip(),
            "count": p.get("count") or 0,
        })
        if len(pathways_top) >= 12:
            break

    # --- stats (rule H) ---
    by_year = pubs.get("by_year") or {}
    recent_years = sorted(by_year.keys(), reverse=True)[:4]
    pubs_recent_years = {y: by_year[y] for y in recent_years}
    stats = {
        "pubs_total": pubs.get("total") or 0,
        "pubs_recent_years": pubs_recent_years,
    }

    # --- notes (rule I), max 5 ---
    notes: list[str] = []
    if not targets_list:
        notes.append("No targets found from sources")
    if drug_type == "biologic":
        notes.append("Biologic: no SMILES")
    if boxed_warning:
        notes.append("Has boxed warning")
    if faers_signal_count is not None:
        notes.append(f"High FAERS signal count: {faers_signal_count}")
    if contraindications_present and "Has contraindications" not in notes:
        notes.append("Has contraindications")
    notes = notes[:5]

    return {
        "drug_id": drug_id,
        "canonical_name": canonical_name,
        "drug_type": drug_type,
        "identifiers": identifiers,
        "structure": structure,
        "targets_top": targets_top,
        "trials": trials_out,
        "safety": safety,
        "pathways_top": pathways_top,
        "stats": stats,
        "notes": notes,
        "version": "short_v1",
    }
