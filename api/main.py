"""FastAPI backend for the procurement auditing system."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import database as db

app = FastAPI(
    title="Procurement Audit API",
    description=(
        "Investigation support API. Scores indicate review priority, "
        "not corruption probability."
    ),
    version="0.1.0",
)


class ReviewIn(BaseModel):
    review_status: str
    investigator_note: str = ""


def _table(name: str):
    conn = db.connect()
    df = db.load_table(conn, name)
    conn.close()
    return df


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/procurements")
def list_procurements(limit: int = 100, offset: int = 0):
    df = _table("procurement_data")
    return df.iloc[offset : offset + limit].fillna("").to_dict(orient="records")


@app.get("/procurements/{publication_id}")
def get_procurement(publication_id: str):
    df = _table("procurement_data")
    hit = df[df.get("publication_id") == publication_id] if "publication_id" in df else df
    if hit.empty:
        raise HTTPException(404, "Not found")
    return hit.fillna("").to_dict(orient="records")


@app.get("/vendors")
def list_vendors(limit: int = 100):
    df = _table("vendor_summary")
    return df.head(limit).fillna("").to_dict(orient="records")


@app.get("/vendors/{vendor_id}")
def get_vendor(vendor_id: str):
    df = _table("vendor_summary")
    hit = df[df["vendor_id"] == vendor_id] if "vendor_id" in df else df
    if hit.empty:
        raise HTTPException(404, "Not found")
    return hit.iloc[0].fillna("").to_dict()


@app.get("/buyers")
def list_buyers(limit: int = 100):
    df = _table("buyer_summary")
    return df.head(limit).fillna("").to_dict(orient="records")


@app.get("/buyers/{buyer_id}")
def get_buyer(buyer_id: str):
    df = _table("buyer_summary")
    hit = df[df["buyer_id"] == buyer_id] if "buyer_id" in df else df
    if hit.empty:
        raise HTTPException(404, "Not found")
    return hit.iloc[0].fillna("").to_dict()


@app.get("/cases")
def list_cases(min_score: float = 0, limit: int = 200):
    df = _table("cases")
    if df.empty:
        return []
    if "priority_score" in df:
        df = df[df["priority_score"] >= min_score].sort_values("priority_score", ascending=False)
    return df.head(limit).fillna("").to_dict(orient="records")


@app.get("/cases/{case_id}")
def get_case(case_id: str):
    df = _table("cases")
    hit = df[df["case_id"] == case_id] if "case_id" in df else df
    if hit.empty:
        raise HTTPException(404, "Not found")
    return hit.iloc[0].fillna("").to_dict()


@app.get("/signals")
def list_signals(limit: int = 200):
    df = _table("signals")
    return df.head(limit).fillna("").to_dict(orient="records")


@app.get("/analytics")
def analytics():
    proc = _table("procurement_data")
    cases = _table("cases")
    return {
        "n_procurements": len(proc),
        "n_cases": len(cases),
        "n_vendors": int(proc["vendor_id"].nunique()) if "vendor_id" in proc else 0,
        "n_buyers": int(proc["buyer_id"].nunique()) if "buyer_id" in proc else 0,
        "disclaimer": "Analytics support investigation prioritization only.",
    }


@app.post("/cases/{case_id}/review")
def review_case(case_id: str, body: ReviewIn):
    conn = db.connect()
    db.init_schema(conn)
    db.upsert_feedback(conn, case_id, body.review_status, body.investigator_note)
    conn.close()
    return {"case_id": case_id, "status": body.review_status}
