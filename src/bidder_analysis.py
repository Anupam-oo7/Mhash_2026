"""Bidder participation analysis — signals, not proof of wrongdoing."""

from __future__ import annotations

import numpy as np
import pandas as pd

import config


def analyze_bidders(procurement: pd.DataFrame, tenders: pd.DataFrame | None = None) -> pd.DataFrame:
    df = procurement.copy()
    if df.empty:
        return df

    df["num_submissions"] = pd.to_numeric(df.get("num_submissions"), errors="coerce")

    peer_bidder_median = (
        df.groupby("peer_group_id")["num_submissions"].transform("median")
        if "peer_group_id" in df.columns
        else np.nan
    )
    df["peer_median_bidders"] = peer_bidder_median

    df["single_bidder"] = df["num_submissions"] == config.SINGLE_BIDDER
    df["low_bidder_count"] = df["num_submissions"].notna() & (
        df["num_submissions"] <= config.LOW_BIDDER_COUNT
    )

    # Bid spread / margin requires multiple bid rows per lot — TED award notices
    # often list only winners. Compute when tenders table has multiple ranks.
    df["bid_spread"] = np.nan
    df["winner_margin"] = np.nan
    df["unusual_bid_margin"] = False

    if tenders is not None and not tenders.empty and "tender_rank" in tenders.columns:
        t = tenders.copy()
        t["bid_amount"] = pd.to_numeric(t.get("bid_amount"), errors="coerce")
        # Group by notice+lot
        for (notice_id, lot_id), g in t.groupby(["notice_id", "lot_id"]):
            amounts = g["bid_amount"].dropna().sort_values()
            if len(amounts) < 2:
                continue
            spread = float(amounts.max() - amounts.min())
            ranked = g.dropna(subset=["tender_rank"]).sort_values("tender_rank")
            margin = np.nan
            if len(ranked) >= 2:
                a0 = ranked.iloc[0]["bid_amount"]
                a1 = ranked.iloc[1]["bid_amount"]
                if pd.notna(a0) and pd.notna(a1) and a0 != 0:
                    margin = abs(a1 - a0) / abs(a0) * 100.0
            mask = (df.get("notice_id") == notice_id) & (df.get("lot_id") == lot_id)
            df.loc[mask, "bid_spread"] = spread
            df.loc[mask, "winner_margin"] = margin
            if pd.notna(margin) and margin <= config.UNUSUAL_BID_MARGIN_PCT:
                df.loc[mask, "unusual_bid_margin"] = True

    return df
