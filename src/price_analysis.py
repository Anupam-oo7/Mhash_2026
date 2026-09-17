"""Robust statistical price analysis relative to peer groups.

Language: unusual deviation vs peers — never 'proves overpricing'.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config


def _robust_z(series: pd.Series, value: float) -> float:
    med = series.median()
    mad = (series - med).abs().median()
    if mad == 0 or pd.isna(mad):
        return np.nan
    return 0.6745 * (value - med) / mad


def analyze_prices(procurement: pd.DataFrame) -> pd.DataFrame:
    df = procurement.copy()
    if df.empty:
        return df

    bid = pd.to_numeric(df.get("bid_amount"), errors="coerce")
    est = pd.to_numeric(df.get("estimated_amount"), errors="coerce")
    df["price_ratio"] = np.where(est > 0, bid / est, np.nan)

    # Peer medians on award/bid amounts
    value_col = bid.fillna(est)
    df["_value"] = value_col

    peer_stats = (
        df.dropna(subset=["peer_group_id"])
        .groupby("peer_group_id")["_value"]
        .agg(
            peer_median="median",
            peer_q1=lambda s: s.quantile(0.25),
            peer_q3=lambda s: s.quantile(0.75),
            peer_count="count",
        )
        .reset_index()
    )
    df = df.merge(peer_stats, on="peer_group_id", how="left")

    df["pct_diff_peer_median"] = np.where(
        df["peer_median"] > 0,
        (df["_value"] - df["peer_median"]) / df["peer_median"] * 100.0,
        np.nan,
    )
    iqr = df["peer_q3"] - df["peer_q1"]
    df["iqr_outlier"] = (
        (df["_value"] < df["peer_q1"] - config.PRICE_IQR_MULTIPLIER * iqr)
        | (df["_value"] > df["peer_q3"] + config.PRICE_IQR_MULTIPLIER * iqr)
    ) & iqr.notna() & (iqr > 0)

    # Percentile within peer group
    df["peer_percentile"] = df.groupby("peer_group_id")["_value"].rank(pct=True)

    df["robust_z"] = (
        df.groupby("peer_group_id")["_value"]
        .transform(lambda s: s.map(lambda v: _robust_z(s.dropna(), float(v)) if pd.notna(v) else np.nan))
    )

    df["price_signal"] = None
    high = df["pct_diff_peer_median"].abs() >= config.PRICE_DEVIATION_PCT_HIGH
    med = df["pct_diff_peer_median"].abs() >= config.PRICE_DEVIATION_PCT_MEDIUM
    df.loc[high, "price_signal"] = "HIGH_DEVIATION"
    df.loc[med & ~high, "price_signal"] = "MEDIUM_DEVIATION"

    # Do not flag when peer evidence is insufficient
    if "insufficient_peer_evidence" in df.columns:
        df.loc[df["insufficient_peer_evidence"] == True, "price_signal"] = None  # noqa: E712

    return df.drop(columns=["_value"], errors="ignore")
