"""Explainable rule-based red-flag engine.

Returns signals with evidence. Never labels entities as corrupt.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import config


def _sev(rule_id: str, triggered: bool, level: str = "MEDIUM") -> dict | None:
    if not triggered:
        return None
    return {"rule_id": rule_id, "severity": level}


def evaluate_rules(row: pd.Series) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []

    # R1 single bidder
    if bool(row.get("single_bidder")):
        signals.append(
            {
                "rule_id": "R1",
                "signal": "SINGLE_BIDDER",
                "severity": "HIGH",
                "evidence": "Only one bidder was recorded.",
                "value": row.get("num_submissions"),
                "threshold": config.SINGLE_BIDDER,
            }
        )

    # R2 very low bidder count
    if bool(row.get("low_bidder_count")) and not bool(row.get("single_bidder")):
        signals.append(
            {
                "rule_id": "R2",
                "signal": "LOW_BIDDER_COUNT",
                "severity": "MEDIUM",
                "evidence": f"Bidder count is {row.get('num_submissions')} (threshold ≤ {config.LOW_BIDDER_COUNT}).",
                "value": row.get("num_submissions"),
                "threshold": config.LOW_BIDDER_COUNT,
            }
        )

    # R3 peer price deviation
    pct = row.get("pct_diff_peer_median")
    if pd.notna(pct) and abs(pct) >= config.PRICE_DEVIATION_PCT_MEDIUM:
        sev = "HIGH" if abs(pct) >= config.PRICE_DEVIATION_PCT_HIGH else "MEDIUM"
        if not bool(row.get("insufficient_peer_evidence")):
            signals.append(
                {
                    "rule_id": "R3",
                    "signal": "PRICE_DEVIATION",
                    "severity": sev,
                    "evidence": (
                        f"Amount is {pct:.1f}% from peer median "
                        f"(peer median={row.get('peer_median')}, n={row.get('peer_group_size')})."
                    ),
                    "value": float(pct),
                    "threshold": config.PRICE_DEVIATION_PCT_MEDIUM,
                }
            )

    # R4 repeated winner
    if bool(row.get("repeated_winner_flag")):
        signals.append(
            {
                "rule_id": "R4",
                "signal": "REPEATED_WINNER",
                "severity": "MEDIUM",
                "evidence": (
                    f"Vendor win rate {row.get('vendor_win_rate'):.1%} "
                    f"over {int(row.get('vendor_total_tenders') or 0)} recorded procurements."
                ),
                "value": row.get("vendor_win_rate"),
                "threshold": config.REPEATED_WIN_RATE_HIGH,
            }
        )

    # R5 high concentration (buyer-level flag on row if present)
    if bool(row.get("buyer_high_concentration")):
        signals.append(
            {
                "rule_id": "R5",
                "signal": "HIGH_VENDOR_CONCENTRATION",
                "severity": "MEDIUM",
                "evidence": "High award concentration detected for this buyer.",
                "value": row.get("buyer_hhi"),
                "threshold": config.HHI_HIGH,
            }
        )

    # R6 repeated co-bidding
    if bool(row.get("repeated_cobid")):
        signals.append(
            {
                "rule_id": "R6",
                "signal": "REPEATED_COBIDDING",
                "severity": "MEDIUM",
                "evidence": (
                    f"Repeated co-participation detected "
                    f"(max shared weight={row.get('max_cobid_weight')})."
                ),
                "value": row.get("max_cobid_weight"),
                "threshold": config.COBID_MIN_SHARED,
            }
        )

    # R7 unusual bid margin
    if bool(row.get("unusual_bid_margin")):
        signals.append(
            {
                "rule_id": "R7",
                "signal": "UNUSUAL_BID_MARGIN",
                "severity": "MEDIUM",
                "evidence": f"Winner margin {row.get('winner_margin')}% is unusually small.",
                "value": row.get("winner_margin"),
                "threshold": config.UNUSUAL_BID_MARGIN_PCT,
            }
        )

    # R9 repeated buyer-vendor
    if bool(row.get("repeated_buyer_vendor")):
        signals.append(
            {
                "rule_id": "R9",
                "signal": "REPEATED_BUYER_VENDOR",
                "severity": "MEDIUM",
                "evidence": (
                    f"Buyer-vendor pair appears {int(row.get('buyer_vendor_count') or 0)} times."
                ),
                "value": row.get("buyer_vendor_count"),
                "threshold": config.BUYER_VENDOR_REPEAT_MIN,
            }
        )

    # R10 unusual contract amount (IQR outlier)
    if bool(row.get("iqr_outlier")) and not bool(row.get("insufficient_peer_evidence")):
        signals.append(
            {
                "rule_id": "R10",
                "signal": "UNUSUAL_CONTRACT_AMOUNT",
                "severity": "MEDIUM",
                "evidence": "Contract amount is an IQR outlier relative to its peer group.",
                "value": row.get("bid_amount") or row.get("estimated_amount"),
                "threshold": f"IQR x {config.PRICE_IQR_MULTIPLIER}",
            }
        )

    # R11 multiple anomalies together
    if len(signals) >= 3:
        signals.append(
            {
                "rule_id": "R11",
                "signal": "MULTIPLE_ANOMALIES",
                "severity": "HIGH",
                "evidence": f"{len(signals)} distinct signals co-occur on this procurement.",
                "value": len(signals),
                "threshold": 3,
            }
        )

    return signals


def apply_rules(procurement: pd.DataFrame, buyer_summary: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = procurement.copy()
    if buyer_summary is not None and not buyer_summary.empty and "buyer_id" in df.columns:
        bs = buyer_summary.set_index("buyer_id")
        df["buyer_high_concentration"] = df["buyer_id"].map(bs.get("high_concentration", pd.Series(dtype=bool))).fillna(False)
        df["buyer_hhi"] = df["buyer_id"].map(bs.get("hhi", pd.Series(dtype=float)))

    signal_rows = []
    signal_lists = []
    for idx, row in df.iterrows():
        sigs = evaluate_rules(row)
        signal_lists.append(sigs)
        for s in sigs:
            signal_rows.append(
                {
                    "row_index": idx,
                    "notice_id": row.get("notice_id"),
                    "publication_id": row.get("publication_id"),
                    "lot_id": row.get("lot_id"),
                    "tender_id": row.get("tender_id"),
                    "buyer_id": row.get("buyer_id"),
                    "vendor_id": row.get("vendor_id"),
                    **s,
                }
            )

    df["signals"] = signal_lists
    df["n_signals"] = df["signals"].map(len)
    signals_df = pd.DataFrame(signal_rows)
    return df, signals_df
