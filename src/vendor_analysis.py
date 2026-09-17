"""Vendor concentration and behavioral history analysis."""

from __future__ import annotations

import numpy as np
import pandas as pd

import config


def build_vendor_summary(procurement: pd.DataFrame) -> pd.DataFrame:
    df = procurement.copy()
    if df.empty or "vendor_id" not in df.columns:
        return pd.DataFrame()

    awarded = df[df.get("notice_root", pd.Series(dtype=str)).eq("ContractAwardNotice") | df.get("tender_status").eq("awarded")]
    # Participation proxy: rows with vendor_id
    base = df.dropna(subset=["vendor_id"])

    rows = []
    for vendor_id, g in base.groupby("vendor_id"):
        wins = g
        if "tender_status" in g.columns:
            wins = g[g["tender_status"].eq("awarded") | g["notice_root"].eq("ContractAwardNotice")]
        total = len(g)
        win_n = len(wins)
        value = pd.to_numeric(wins.get("bid_amount"), errors="coerce").fillna(
            pd.to_numeric(wins.get("estimated_amount"), errors="coerce")
        )
        rows.append(
            {
                "vendor_id": vendor_id,
                "vendor_name": g["vendor_name"].dropna().iloc[0] if g["vendor_name"].notna().any() else None,
                "total_tenders": total,
                "total_wins": win_n,
                "win_rate": win_n / total if total else np.nan,
                "total_contract_value": float(value.sum()) if len(value) else 0.0,
                "average_contract_value": float(value.mean()) if len(value) else np.nan,
                "n_buyers": g["buyer_id"].nunique() if "buyer_id" in g else 0,
                "n_cpv": g["cpv_code"].nunique() if "cpv_code" in g else 0,
                "n_regions": g["performance_region"].nunique() if "performance_region" in g else 0,
                "main_cpv": g["cpv_code"].mode().iloc[0] if g["cpv_code"].notna().any() else None,
                "main_buyer": g["buyer_id"].mode().iloc[0] if g["buyer_id"].notna().any() else None,
            }
        )
    return pd.DataFrame(rows)


def build_buyer_summary(procurement: pd.DataFrame) -> pd.DataFrame:
    df = procurement.copy()
    if df.empty or "buyer_id" not in df.columns:
        return pd.DataFrame()

    rows = []
    for buyer_id, g in df.dropna(subset=["buyer_id"]).groupby("buyer_id"):
        values = pd.to_numeric(g.get("bid_amount"), errors="coerce").fillna(
            pd.to_numeric(g.get("estimated_amount"), errors="coerce")
        )
        vendor_counts = g["vendor_id"].value_counts(dropna=True)
        top_share = float(vendor_counts.iloc[0] / vendor_counts.sum()) if len(vendor_counts) else np.nan
        # HHI on vendor award shares
        shares = vendor_counts / vendor_counts.sum() if len(vendor_counts) else pd.Series(dtype=float)
        hhi = float((shares ** 2).sum()) if len(shares) else np.nan
        rows.append(
            {
                "buyer_id": buyer_id,
                "buyer_name": g["buyer_name"].dropna().iloc[0] if g["buyer_name"].notna().any() else None,
                "total_procurements": len(g),
                "total_value": float(values.sum()) if len(values) else 0.0,
                "n_vendors": g["vendor_id"].nunique(),
                "average_bidders": float(pd.to_numeric(g.get("num_submissions"), errors="coerce").mean()),
                "top_vendor_share": top_share,
                "top3_vendor_share": float(shares.head(3).sum()) if len(shares) else np.nan,
                "hhi": hhi,
                "high_concentration": bool(pd.notna(hhi) and hhi >= config.HHI_HIGH)
                or bool(pd.notna(top_share) and top_share >= config.TOP_VENDOR_SHARE_HIGH),
                "main_cpv": g["cpv_code"].mode().iloc[0] if g["cpv_code"].notna().any() else None,
            }
        )
    return pd.DataFrame(rows)


def build_vendor_history(procurement: pd.DataFrame) -> pd.DataFrame:
    df = procurement.copy()
    if df.empty or "vendor_id" not in df.columns:
        return pd.DataFrame()
    cols = [
        c
        for c in (
            "vendor_id",
            "vendor_name",
            "buyer_id",
            "buyer_name",
            "notice_id",
            "publication_id",
            "lot_id",
            "tender_id",
            "publication_date",
            "award_date",
            "bid_amount",
            "estimated_amount",
            "cpv_code",
            "performance_country",
            "tender_status",
            "notice_root",
        )
        if c in df.columns
    ]
    hist = df.dropna(subset=["vendor_id"])[cols].copy()
    hist = hist.sort_values(
        by=[c for c in ("publication_date", "award_date") if c in hist.columns]
    )
    return hist


def annotate_vendor_behavior(procurement: pd.DataFrame, vendor_summary: pd.DataFrame) -> pd.DataFrame:
    df = procurement.copy()
    if df.empty or vendor_summary.empty:
        df["vendor_win_rate"] = np.nan
        df["repeated_winner_flag"] = False
        return df

    vs = vendor_summary.set_index("vendor_id")
    df["vendor_win_rate"] = df["vendor_id"].map(vs["win_rate"])
    df["vendor_total_wins"] = df["vendor_id"].map(vs["total_wins"])
    df["vendor_total_tenders"] = df["vendor_id"].map(vs["total_tenders"])

    # Repeated winner: high win rate with enough history
    df["repeated_winner_flag"] = (
        (df["vendor_win_rate"] >= config.REPEATED_WIN_RATE_HIGH)
        & (df["vendor_total_tenders"] >= config.REPEATED_WIN_MIN_TENDERS)
    )

    # Buyer-vendor frequency
    if "buyer_id" in df.columns and "vendor_id" in df.columns:
        pair_counts = (
            df.dropna(subset=["buyer_id", "vendor_id"])
            .groupby(["buyer_id", "vendor_id"])
            .size()
            .rename("buyer_vendor_count")
            .reset_index()
        )
        df = df.merge(pair_counts, on=["buyer_id", "vendor_id"], how="left")
        df["repeated_buyer_vendor"] = df["buyer_vendor_count"] >= config.BUYER_VENDOR_REPEAT_MIN
    else:
        df["buyer_vendor_count"] = np.nan
        df["repeated_buyer_vendor"] = False

    return df
