"""SQLite persistence for the procurement auditing system."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

import pandas as pd

import config

TABLES = [
    "notices",
    "lots",
    "tenders",
    "organizations",
    "relationships",
    "procurement_data",
    "vendor_summary",
    "buyer_summary",
    "signals",
    "cases",
    "investigator_feedback",
    "entity_resolution",
]


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS investigator_feedback (
            case_id TEXT PRIMARY KEY,
            review_status TEXT,
            investigator_note TEXT,
            review_date TEXT
        )
        """
    )
    conn.commit()


def write_dataframe(conn: sqlite3.Connection, name: str, df: pd.DataFrame) -> None:
    if df is None:
        return
    # Convert non-SQL-friendly columns
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == "object":
            # stringify nested lists/dicts
            out[col] = out[col].map(
                lambda x: x if x is None or isinstance(x, (str, int, float, bool)) else str(x)
            )
    out.to_sql(name, conn, if_exists="replace", index=False)


def load_table(conn: sqlite3.Connection, name: str) -> pd.DataFrame:
    try:
        return pd.read_sql_query(f"SELECT * FROM {name}", conn)
    except Exception:
        return pd.DataFrame()


def upsert_feedback(
    conn: sqlite3.Connection,
    case_id: str,
    review_status: str,
    investigator_note: str = "",
    review_date: str | None = None,
) -> None:
    import datetime as dt

    review_date = review_date or dt.datetime.utcnow().isoformat()
    conn.execute(
        """
        INSERT INTO investigator_feedback (case_id, review_status, investigator_note, review_date)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(case_id) DO UPDATE SET
            review_status=excluded.review_status,
            investigator_note=excluded.investigator_note,
            review_date=excluded.review_date
        """,
        (case_id, review_status, investigator_note, review_date),
    )
    # Also update cases table if present
    try:
        conn.execute(
            "UPDATE cases SET review_status=?, investigator_note=? WHERE case_id=?",
            (review_status, investigator_note, case_id),
        )
    except Exception:
        pass
    conn.commit()
