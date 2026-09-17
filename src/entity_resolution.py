"""Organization / vendor entity resolution.

Does NOT auto-merge on similar names alone. Uses IDs and registration numbers
as stronger signals and assigns an explicit confidence score.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import pandas as pd

import config


def normalize_name(name: Any) -> str:
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    s = str(name)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    # Collapse dotted legal forms before stripping punctuation (s.r.l. → srl)
    for dotted, canon in (
        ("s.r.l.", "srl"),
        ("s.r.l", "srl"),
        ("s.p.a.", "spa"),
        ("s.p.a", "spa"),
        ("l.l.c.", "llc"),
        ("l.l.c", "llc"),
        ("s.a.", "sa"),
        ("ltd.", "limited"),
        ("inc.", "inc"),
        ("pvt.", "private"),
    ):
        s = s.replace(dotted, f" {canon} ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = []
    for tok in s.split():
        tokens.append(config.LEGAL_SUFFIXES.get(tok, tok))
    return " ".join(tokens)


def strip_legal_suffix(normalized: str) -> str:
    parts = normalized.split()
    suffixes = set(config.LEGAL_SUFFIXES.values())
    while parts and parts[-1] in suffixes:
        parts.pop()
    return " ".join(parts)


def resolve_entities(organizations: pd.DataFrame) -> pd.DataFrame:
    """Return entity resolution table with confidence.

    entity_id preference:
      1. company_id + country (high)
      2. org_id within same notice context is local only — globally use company_id
      3. normalized name + country (medium)
      4. normalized name only (low — review required)
    """
    if organizations is None or organizations.empty:
        return pd.DataFrame(
            columns=[
                "org_id",
                "original_name",
                "normalized_name",
                "name_key",
                "company_id",
                "country",
                "entity_id",
                "match_method",
                "confidence",
            ]
        )

    df = organizations.copy()
    # Collapse to unique org appearances
    cols = [c for c in ("org_id", "name", "company_id", "country", "city", "street") if c in df.columns]
    df = df[cols].drop_duplicates()

    df["original_name"] = df["name"]
    df["normalized_name"] = df["name"].map(normalize_name)
    df["name_key"] = df["normalized_name"].map(strip_legal_suffix)

    entity_ids: dict[tuple, str] = {}
    rows = []

    for _, r in df.iterrows():
        company_id = r.get("company_id")
        country = r.get("country")
        name_key = r.get("name_key") or ""
        org_id = r.get("org_id")

        confidence = 0.2
        method = "weak_name"
        key = None

        if pd.notna(company_id) and str(company_id).strip():
            key = ("reg", str(company_id).strip().upper(), str(country or "").upper())
            confidence = 0.95
            method = "company_registration_id"
        elif name_key and pd.notna(country) and str(country).strip():
            key = ("name_country", name_key, str(country).upper())
            confidence = 0.75
            method = "normalized_name_country"
        elif name_key:
            key = ("name_only", name_key)
            confidence = 0.35
            method = "normalized_name_only"
        else:
            key = ("org_local", str(org_id))
            confidence = 0.25
            method = "local_org_id"

        if key not in entity_ids:
            entity_ids[key] = f"ENT-{len(entity_ids)+1:06d}"
        entity_id = entity_ids[key]

        rows.append(
            {
                "org_id": org_id,
                "original_name": r.get("original_name"),
                "normalized_name": r.get("normalized_name"),
                "name_key": name_key,
                "company_id": company_id,
                "country": country,
                "city": r.get("city"),
                "street": r.get("street"),
                "entity_id": entity_id,
                "match_method": method,
                "confidence": confidence,
            }
        )

    out = pd.DataFrame(rows).drop_duplicates(subset=["org_id", "entity_id", "company_id"])
    return out
