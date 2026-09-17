"""Peer / context engine for comparable procurements.

Avoids comparing unrelated markets (e.g. medical equipment vs road works).
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

import config

Strictness = Literal["strict", "medium", "broad"]


def _cpv_key(cpv: object, strictness: Strictness) -> str:
    if cpv is None or (isinstance(cpv, float) and np.isnan(cpv)):
        return "NA"
    s = str(cpv)
    if strictness == "strict":
        return s
    if strictness == "medium":
        return s[:4] if len(s) >= 4 else s
    return s[:2] if len(s) >= 2 else s


def _safe_str(val: object, default: str = "NA") -> str:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    return s if s and s.lower() != "nan" else default


def _geo_key(row: pd.Series, strictness: Strictness) -> str:
    country = _safe_str(row.get("performance_country"))
    if country == "NA":
        country = _safe_str(row.get("buyer_country"))
    region = _safe_str(row.get("performance_region"))
    if region == "NA":
        region = _safe_str(row.get("buyer_region"))
    if strictness == "strict":
        return f"{country}|{region}"
    return country


def _year_key(row: pd.Series, strictness: Strictness) -> str:
    dt = row.get("publication_date")
    try:
        if pd.isna(dt):
            return "NA"
    except (TypeError, ValueError):
        if dt is None:
            return "NA"
    year = pd.Timestamp(dt).year
    if strictness == "broad":
        return str(year - (year % max(config.PEER_YEAR_WINDOW, 1)))
    return str(year)


def assign_peer_groups(
    procurement: pd.DataFrame,
    strictness: Strictness | None = None,
) -> pd.DataFrame:
    """Add peer_group_id and peer_group_size columns."""
    strictness = strictness or config.PEER_STRICTNESS  # type: ignore[assignment]
    df = procurement.copy()
    if df.empty:
        df["peer_group_id"] = pd.Series(dtype="string")
        df["peer_group_size"] = pd.Series(dtype="int")
        df["peer_strictness"] = pd.Series(dtype="string")
        return df

    df["_cpv_key"] = df.get("cpv_code", pd.Series([None] * len(df))).map(
        lambda x: _cpv_key(x, strictness)
    )
    df["_geo_key"] = df.apply(lambda r: _geo_key(r, strictness), axis=1)
    df["_year_key"] = df.apply(lambda r: _year_key(r, strictness), axis=1)
    df["_ptype"] = df.get("procurement_type", pd.Series(["NA"] * len(df))).fillna("NA")

    # Value band: discretize log estimated amount for medium/broad
    amounts = pd.to_numeric(df.get("estimated_amount"), errors="coerce")
    if strictness == "strict":
        # Fine bins
        df["_value_band"] = pd.cut(
            np.log10(amounts.clip(lower=1)),
            bins=[-np.inf, 4, 5, 5.5, 6, 6.5, 7, np.inf],
            labels=["<10k", "10-100k", "100-300k", "300k-1M", "1-3M", "3-10M", ">10M"],
        ).astype(str)
    else:
        df["_value_band"] = pd.cut(
            np.log10(amounts.clip(lower=1)),
            bins=[-np.inf, 5, 6, 7, np.inf],
            labels=["<100k", "100k-1M", "1-10M", ">10M"],
        ).astype(str)

    df["peer_group_id"] = (
        "PG|"
        + df["_cpv_key"].astype(str)
        + "|"
        + df["_geo_key"].astype(str)
        + "|"
        + df["_year_key"].astype(str)
        + "|"
        + df["_ptype"].astype(str)
        + "|"
        + df["_value_band"].astype(str)
    )
    sizes = df.groupby("peer_group_id")["peer_group_id"].transform("size")
    df["peer_group_size"] = sizes
    df["peer_strictness"] = strictness
    df["insufficient_peer_evidence"] = df["peer_group_size"] < config.PEER_MIN_GROUP_SIZE

    drop_cols = [c for c in df.columns if c.startswith("_")]
    return df.drop(columns=drop_cols)
