"""Human-readable explanations for flagged procurements / cases."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd


def _txt(val, default: str = "") -> str:
    try:
        if val is None or (isinstance(val, float) and pd.isna(val)) or pd.isna(val):
            return default
    except (TypeError, ValueError):
        if val is None:
            return default
    return str(val)


def explain_row(row: pd.Series) -> str:
    lines = []
    pub = _txt(row.get("publication_id") or row.get("notice_id"), "UNKNOWN")
    if pub == "UNKNOWN":
        pub = _txt(row.get("notice_id"), "UNKNOWN")
    score = row.get("investigation_priority_score", row.get("priority_score"))
    level = _txt(row.get("priority_level"))
    conf = _txt(row.get("data_confidence"))

    lines.append(f"Procurement: {pub} / lot {row.get('lot_id')} / tender {row.get('tender_id')}")
    lines.append(f"Buyer: {_txt(row.get('buyer_name'))} ({_txt(row.get('buyer_id'))})")
    lines.append(f"Vendor: {_txt(row.get('vendor_name'))} ({_txt(row.get('vendor_id'))})")
    val = row.get("bid_amount")
    try:
        if pd.isna(val):
            val = row.get("estimated_amount")
    except (TypeError, ValueError):
        pass
    try:
        if pd.isna(val):
            val = row.get("contract_value")
    except (TypeError, ValueError):
        pass
    cur = _txt(row.get("currency"))
    lines.append(f"Contract value: {cur} {val}")
    lines.append(f"Investigation Priority: {score}/100 ({level})")
    lines.append(f"Evidence confidence: {conf}")
    lines.append("")
    lines.append("Reasons:")

    signals = row.get("signals")
    if signals is None:
        raw = row.get("signals_json")
        if raw is not None and not (isinstance(raw, float) and pd.isna(raw)):
            try:
                if not pd.isna(raw):
                    signals = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                signals = []
    signals = signals or []

    if not signals:
        lines.append("1. No rule signals — score driven by statistical/ML components if present.")
    else:
        for i, s in enumerate(signals, 1):
            lines.append(f"{i}. [{s.get('rule_id')}] {s.get('signal')} ({s.get('severity')})")
            lines.append(f"   {s.get('evidence')}")
            lines.append(f"   value={s.get('value')} threshold={s.get('threshold')}")

    # Peer context
    try:
        has_peer = not pd.isna(row.get("peer_group_id"))
    except (TypeError, ValueError):
        has_peer = row.get("peer_group_id") is not None
    if has_peer and _txt(row.get("peer_group_id")):
        lines.append("")
        lines.append("Peer comparison:")
        lines.append(f"- peer_group_id: {row.get('peer_group_id')}")
        lines.append(f"- peer size: {row.get('peer_group_size')}")
        try:
            if not pd.isna(row.get("peer_median")):
                lines.append(f"- peer median: {row.get('peer_median')}")
        except (TypeError, ValueError):
            pass
        try:
            if not pd.isna(row.get("pct_diff_peer_median")):
                lines.append(f"- pct vs median: {row.get('pct_diff_peer_median'):.1f}%")
        except (TypeError, ValueError):
            pass

    try:
        if not pd.isna(row.get("ml_anomaly_score")):
            lines.append("")
            lines.append(f"ML behavioral anomaly score: {row.get('ml_anomaly_score'):.1f}/100")
            lines.append("(This is not a probability of corruption.)")
    except (TypeError, ValueError):
        pass

    lines.append("")
    lines.append(
        "IMPORTANT: These signals indicate unusual procurement patterns that may "
        "warrant review. They do not establish misconduct or corruption."
    )
    return "\n".join(lines)


def build_explanations(cases: pd.DataFrame, procurement: pd.DataFrame | None = None) -> pd.DataFrame:
    if cases is None or cases.empty:
        return pd.DataFrame(columns=["case_id", "explanation"])

    # Optionally enrich from procurement
    enriched = cases.copy()
    if procurement is not None and not procurement.empty:
        keys = ["publication_id", "lot_id", "tender_id"]
        if all(k in procurement.columns for k in keys) and all(k in enriched.columns for k in keys):
            cols = [
                c
                for c in procurement.columns
                if c
                in keys
                + [
                    "signals",
                    "peer_median",
                    "pct_diff_peer_median",
                    "peer_group_id",
                    "peer_group_size",
                    "ml_anomaly_score",
                    "bid_amount",
                    "estimated_amount",
                    "currency",
                    "investigation_priority_score",
                    "priority_level",
                    "data_confidence",
                    "buyer_name",
                    "vendor_name",
                    "buyer_id",
                    "vendor_id",
                ]
            ]
            enriched = enriched.merge(
                procurement[cols].drop_duplicates(keys),
                on=keys,
                how="left",
                suffixes=("", "_proc"),
            )

    rows = []
    for _, row in enriched.iterrows():
        rows.append({"case_id": row.get("case_id"), "explanation": explain_row(row)})
    return pd.DataFrame(rows)
