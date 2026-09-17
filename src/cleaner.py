"""Data cleaning and quality statistics for extracted procurement tables."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _to_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True)


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def standardize_country(code: Any) -> Any:
    if code is None or (isinstance(code, float) and np.isnan(code)):
        return pd.NA
    s = str(code).strip().upper()
    # TED uses ISO 3166-1 alpha-3 in IdentificationCode (e.g. ITA)
    aliases = {"IT": "ITA", "FR": "FRA", "DE": "DEU", "ES": "ESP", "NL": "NLD", "BE": "BEL"}
    return aliases.get(s, s)


def standardize_cpv(code: Any) -> Any:
    if code is None or (isinstance(code, float) and np.isnan(code)):
        return pd.NA
    s = str(code).strip()
    # Keep digits only; CPV is typically 8 digits (+ check digit sometimes embedded)
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits if digits else pd.NA


def clean_procurement_tables(
    tables: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Clean all tables; return cleaned tables and quality report."""
    cleaned: dict[str, pd.DataFrame] = {}
    quality: dict[str, Any] = {"tables": {}}

    for name, df in tables.items():
        if df is None or df.empty:
            cleaned[name] = df.copy() if df is not None else pd.DataFrame()
            quality["tables"][name] = {"rows": 0, "note": "empty"}
            continue
        out = df.copy()
        before = len(out)

        # Dates
        for col in ("publication_date", "award_date", "issue_date", "contract_issue_date"):
            if col in out.columns:
                out[f"{col}_raw"] = out[col]
                out[col] = _to_datetime(out[col])

        # Numerics
        for col in (
            "bid_amount",
            "estimated_amount",
            "payable_amount",
            "framework_max_amount",
            "num_submissions",
            "num_sme_submissions",
            "tender_rank",
        ):
            if col in out.columns:
                out[f"{col}_raw"] = out[col]
                out[col] = _to_numeric(out[col])

        # Country / region / CPV
        for col in ("buyer_country", "performance_country", "country"):
            if col in out.columns:
                out[col] = out[col].map(standardize_country)
        for col in ("buyer_region", "performance_region", "region"):
            if col in out.columns:
                out[col] = out[col].astype("string").str.strip().str.upper()
        if "cpv_code" in out.columns:
            out["cpv_code"] = out["cpv_code"].map(standardize_cpv)
            out["cpv_family"] = out["cpv_code"].astype("string").str[:2]

        if "currency" in out.columns:
            out["currency"] = out["currency"].astype("string").str.strip().str.upper()

        # Impossible amounts
        invalid_amounts = 0
        for col in ("bid_amount", "estimated_amount"):
            if col in out.columns:
                mask = out[col].notna() & (out[col] < 0)
                invalid_amounts += int(mask.sum())
                out.loc[mask, f"{col}_invalid"] = True
                out.loc[mask, col] = np.nan

        # Deduplicate exact rows
        out = out.drop_duplicates()
        after = len(out)

        missing_pct = {
            c: float(out[c].isna().mean() * 100) for c in out.columns if not c.endswith("_raw")
        }

        quality["tables"][name] = {
            "rows_before": before,
            "rows_after": after,
            "duplicates_removed": before - after,
            "invalid_amounts": invalid_amounts,
            "missing_pct": missing_pct,
        }
        cleaned[name] = out

    # Cross-table summary from procurement_data
    proc = cleaned.get("procurement_data", pd.DataFrame())
    summary = {
        "unique_vendors": int(proc["vendor_id"].nunique()) if "vendor_id" in proc else 0,
        "unique_buyers": int(proc["buyer_id"].nunique()) if "buyer_id" in proc else 0,
        "unique_cpv": int(proc["cpv_code"].nunique()) if "cpv_code" in proc else 0,
        "unique_notices": int(proc["notice_id"].nunique()) if "notice_id" in proc else 0,
    }
    if "publication_date" in proc.columns:
        summary["invalid_publication_dates"] = int(proc["publication_date"].isna().sum())
    quality["summary"] = summary
    return cleaned, quality


def save_quality_report(quality: dict[str, Any], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Data Quality Report", ""]
    summary = quality.get("summary", {})
    lines.append("## Summary")
    for k, v in summary.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    for table, meta in quality.get("tables", {}).items():
        lines.append(f"## {table}")
        for k, v in meta.items():
            if k == "missing_pct":
                lines.append("- missing_pct:")
                for col, pct in sorted(v.items(), key=lambda x: -x[1])[:30]:
                    lines.append(f"  - {col}: {pct:.1f}%")
            else:
                lines.append(f"- {k}: {v}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
