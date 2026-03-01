"""
Text processing helpers: snippet extraction, keyword scanning, etc.
"""
from __future__ import annotations

import re


def extract_snippet(text: str, keyword: str, window: int = 120) -> str:
    """
    Return a ±window character snippet around the first occurrence of keyword.
    Falls back to the first `window` characters if not found.
    """
    if not text:
        return ""
    lower_text = text.lower()
    lower_kw = keyword.lower()
    idx = lower_text.find(lower_kw)
    if idx == -1:
        return text[:window].strip()
    start = max(0, idx - window // 2)
    end = min(len(text), idx + len(keyword) + window // 2)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def find_keywords(text: str, keywords: list[str]) -> list[str]:
    """Return all keywords (case-insensitive) that appear in text."""
    if not text:
        return []
    lower_text = text.lower()
    found = []
    for kw in keywords:
        pattern = r"\b" + re.escape(kw.lower()) + r"\b"
        if re.search(pattern, lower_text):
            found.append(kw)
    return found


def word_count(text: str | None) -> int:
    if not text:
        return 0
    return len(text.split())


def join_authors(authors: list[dict] | list[str] | None) -> list[str]:
    """Normalise a heterogeneous authors list to a flat list of strings."""
    if not authors:
        return []
    result = []
    for a in authors:
        if isinstance(a, str):
            result.append(a)
        elif isinstance(a, dict):
            parts = [a.get("last_name", ""), a.get("fore_name", a.get("first_name", ""))]
            name = " ".join(p for p in parts if p).strip()
            result.append(name or str(a))
    return result
