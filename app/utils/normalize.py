"""
Text and identifier normalization utilities.
"""
from __future__ import annotations

import re
import unicodedata


def normalize_drug_name(name: str) -> str:
    """Lowercase, strip, collapse whitespace, normalize unicode."""
    name = unicodedata.normalize("NFKC", name)
    name = name.strip().lower()
    name = re.sub(r"\s+", " ", name)
    return name


def generate_name_variants(name: str) -> set[str]:
    """
    Generate common spelling/formatting variants of a drug name.
    Returns a set including the original (lowercased).
    """
    variants: set[str] = set()
    base = normalize_drug_name(name)
    variants.add(base)

    # Original casing
    variants.add(name.strip())

    # Remove hyphens / spaces
    no_hyphen = base.replace("-", "")
    no_space = base.replace(" ", "")
    no_both = base.replace("-", "").replace(" ", "")
    variants.update({no_hyphen, no_space, no_both})

    # Hyphen ↔ space swap
    variants.add(base.replace("-", " "))
    variants.add(base.replace(" ", "-"))

    # Common salt suffixes to strip
    salt_suffixes = [
        " hydrochloride", " hcl", " sodium", " potassium",
        " sulfate", " sulphate", " phosphate", " acetate",
        " maleate", " fumarate", " tartrate", " citrate",
        " mesylate", " tosylate", " succinate", " besylate",
    ]
    for suffix in salt_suffixes:
        if base.endswith(suffix):
            stripped = base[: -len(suffix)].strip()
            if stripped:
                variants.add(stripped)
                variants.update(generate_name_variants(stripped))

    # Remove trailing numbers / codes like "-2", " 2"
    cleaned = re.sub(r"[-\s]\d+$", "", base).strip()
    if cleaned and cleaned != base:
        variants.add(cleaned)

    # Remove empty strings
    variants.discard("")
    return variants


def clean_text(text: str | None) -> str | None:
    """Strip and collapse whitespace from free text."""
    if text is None:
        return None
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text or None


def truncate(text: str | None, max_len: int = 2048) -> str | None:
    if not text:
        return text
    return text[:max_len]
