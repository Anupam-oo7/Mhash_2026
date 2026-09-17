"""Evidence fusion and investigation-priority scoring.

Scores direct investigative attention. They are NOT corruption probabilities.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

import config


def _priority_level(score: float) -> str:
    for name, (lo, hi) in config.PRIORITY_BANDS.items():
        if lo <= score <= hi:
            return name
    return "low"


def _data_confidence(row: pd.Series) -> tuple[str, float]:
    """Separate from investigation priority: how reliable is the evidence?"""
    conf = 1.0
    reasons = []

    peer_size = row.get("peer_group_size")
    if pd.isna(peer_size) or peer_size < config.LOW_PEER_SIZE_THRESHOLD:
        conf *= config.CONFIDENCE_PENALTY_LOW_PEERS
        reasons.append("Insufficient peer evidence")

    cpv = str(row.get("cpv_code") or "")
    if any(cpv.startswith(p) for p in config.SPECIALIZED_CPV_PREFIXES):
        # Specialized markets: reduce confidence of competition-based signals
        conf *= config.CONFIDENCE_PENALTY_SPECIALIZED
        reasons.append("Specialized CPV category")

    if pd.isna(row.get("bid_amount")) and pd.isna(row.get("estimated_amount")):
        conf *= 0.6
        reasons.append("Missing amount fields")

    if conf >= 0.75:
        level = "HIGH"
    elif conf >= 0.45:
        level = "MEDIUM"
    else:
        level = "LOW"
    return level, conf


def score_row(row: pd.Series) -> dict[str, Any]:
    signals = row.get("signals") or []
    if isinstance(signals, str):
        try:
            signals = json.loads(signals)
        except json.JSONDecodeError:
            signals = []

    rule_score = 0.0
    for s in signals:
        rid = s.get("rule_id")
        w = config.RULE_WEIGHTS.get(rid, 5)
        sev = s.get("severity", "MEDIUM")
        mult = 1.0 if sev == "MEDIUM" else 1.25 if sev == "HIGH" else 0.75
        # Specialized market dampens low-bidder rules
        cpv = str(row.get("cpv_code") or "")
        if rid in ("R1", "R2") and any(cpv.startswith(p) for p in config.SPECIALIZED_CPV_PREFIXES):
            mult *= 0.6
        if bool(row.get("insufficient_peer_evidence")) and rid in ("R3", "R10"):
            mult *= 0.3
        rule_score += w * mult
    rule_score = min(rule_score, config.SCORE_CAPS["rule_score"])

    # Statistical component from price deviation / IQR
    stat = 0.0
    pct = row.get("pct_diff_peer_median")
    if pd.notna(pct) and not bool(row.get("insufficient_peer_evidence")):
        stat += min(abs(pct) / 2.0, 20)
    if bool(row.get("iqr_outlier")):
        stat += 5
    statistical_score = min(stat, config.SCORE_CAPS["statistical_score"])

    behavior = 0.0
    if bool(row.get("repeated_winner_flag")):
        behavior += 10
    if bool(row.get("repeated_buyer_vendor")):
        behavior += 8
    behavior_score = min(behavior, config.SCORE_CAPS["behavior_score"])

    network = 0.0
    if bool(row.get("repeated_cobid")):
        network += min(float(row.get("max_cobid_weight") or 0) * 2, 15)
    network_score = min(network, config.SCORE_CAPS["network_score"])

    ml = row.get("ml_anomaly_score")
    ml_score = 0.0
    if pd.notna(ml):
        ml_score = min(float(ml) * (config.SCORE_CAPS["ml_score"] / 100.0), config.SCORE_CAPS["ml_score"])
        if not bool(row.get("ml_is_outlier")):
            ml_score *= 0.5

    total = rule_score + statistical_score + behavior_score + network_score + ml_score
    # Soft cap to 100
    investigation_priority_score = float(min(100.0, total))

    conf_level, conf_val = _data_confidence(row)

    return {
        "rule_score": round(rule_score, 2),
        "statistical_score": round(statistical_score, 2),
        "behavior_score": round(behavior_score, 2),
        "network_score": round(network_score, 2),
        "ml_score": round(ml_score, 2),
        "investigation_priority_score": round(investigation_priority_score, 2),
        "priority_level": _priority_level(investigation_priority_score),
        "data_confidence": conf_level,
        "data_confidence_value": round(conf_val, 3),
    }


def apply_scoring(procurement: pd.DataFrame) -> pd.DataFrame:
    df = procurement.copy()
    if df.empty:
        return df
    scored = df.apply(lambda r: pd.Series(score_row(r)), axis=1)
    return pd.concat([df.reset_index(drop=True), scored.reset_index(drop=True)], axis=1)


def generate_cases(procurement: pd.DataFrame, min_score: float = 31.0) -> pd.DataFrame:
    df = procurement.copy()
    if df.empty or "investigation_priority_score" not in df.columns:
        return pd.DataFrame()

    flagged = df[df["investigation_priority_score"] >= min_score].copy()
    flagged = flagged.sort_values("investigation_priority_score", ascending=False)
    cases = []
    for i, (_, row) in enumerate(flagged.iterrows(), start=1):
        signals = row.get("signals") or []
        cases.append(
            {
                "case_id": f"CASE-{i:04d}",
                "procurement_key": f"{row.get('publication_id')}|{row.get('lot_id')}|{row.get('tender_id')}",
                "notice_id": row.get("notice_id"),
                "publication_id": row.get("publication_id"),
                "lot_id": row.get("lot_id"),
                "tender_id": row.get("tender_id"),
                "priority_score": row.get("investigation_priority_score"),
                "priority_level": row.get("priority_level"),
                "data_confidence": row.get("data_confidence"),
                "buyer_id": row.get("buyer_id"),
                "buyer_name": row.get("buyer_name"),
                "vendor_id": row.get("vendor_id"),
                "vendor_name": row.get("vendor_name"),
                "contract_value": row.get("bid_amount") if pd.notna(row.get("bid_amount")) else row.get("estimated_amount"),
                "currency": row.get("currency"),
                "cpv_code": row.get("cpv_code"),
                "publication_date": row.get("publication_date"),
                "n_signals": row.get("n_signals"),
                "signals_json": json.dumps(signals, default=str),
                "signal_types": "|".join(sorted({s.get("signal", "") for s in signals})),
                "peer_group_id": row.get("peer_group_id"),
                "peer_group_size": row.get("peer_group_size"),
                "review_status": "NEW",
                "investigator_note": "",
                "disclaimer": (
                    "These signals indicate unusual procurement patterns that may warrant "
                    "review. They do not establish misconduct or corruption."
                ),
            }
        )
    return pd.DataFrame(cases)
