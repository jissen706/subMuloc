"""
Disease query resolver: parse OMIM/ORPHA from query, optional Orphanet lookup.
Deterministic, no LLM. Always returns something usable.
"""
from __future__ import annotations

import re
from typing import Any

# OMIM: "OMIM:610661" or 6-digit ID
OMIM_PATTERN = re.compile(r"(?:OMIM:)?(\d{6})", re.I)
# ORPHA: "ORPHA:791" or "ORPHANET:791"
ORPHA_PATTERN = re.compile(r"(?:ORPHA|ORPHANET):\s*(\d+)", re.I)


def resolve_disease_query(query: str) -> dict[str, Any]:
    """
    Resolve a disease query to canonical_name, ids, synonyms, resolver_notes.
    Does not touch DB. Returns dict suitable for POST /disease/resolve response.
    """
    q = (query or "").strip()
    if not q:
        return {
            "canonical_name": "",
            "ids": {"orpha": None, "omim": None},
            "synonyms": [],
            "resolver_notes": ["Empty query"],
        }

    ids: dict[str, str | None] = {"orpha": None, "omim": None}
    synonyms: list[str] = []
    notes: list[str] = []

    # OMIM
    omim_m = OMIM_PATTERN.search(q)
    if omim_m:
        ids["omim"] = omim_m.group(1)

    # ORPHA
    orpha_m = ORPHA_PATTERN.search(q)
    if orpha_m:
        ids["orpha"] = orpha_m.group(1)

    # Orphanet best-effort: not configured for hackathon; use query as canonical
    canonical_name = q
    if not ids["orpha"] and not ids["omim"]:
        # Could call Orphanet API here; for now skip and note
        notes.append("Orphanet resolution not configured; using query as canonical_name")

    # Dedupe canonical into synonyms if we have IDs
    if ids["orpha"] or ids["omim"]:
        synonyms = [q] if q else []
    else:
        synonyms = []

    # Cap synonyms at 25, notes at 5
    synonyms = list(dict.fromkeys(synonyms))[:25]
    notes = notes[:5]

    return {
        "canonical_name": canonical_name,
        "ids": ids,
        "synonyms": synonyms,
        "resolver_notes": notes,
    }
