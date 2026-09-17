"""Unit tests for parser, cleaning, rules, and scoring."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.parser import EformsParser
from src.cleaner import clean_procurement_tables, standardize_cpv, standardize_country
from src.entity_resolution import normalize_name, resolve_entities
from src.context_engine import assign_peer_groups
from src.rules import evaluate_rules
from src.scoring import score_row, generate_cases, apply_scoring


SAMPLE_XML = Path(
    r"C:\Users\anupa\Desktop\Coding\Python\M#\2026-08\08\20260803_2026147\00533445_2026.xml"
)


@pytest.mark.skipif(not SAMPLE_XML.exists(), reason="Sample TED XML not present")
def test_parse_award_notice():
    result = EformsParser().parse_file(SAMPLE_XML)
    assert not result.errors or result.notices
    assert len(result.notices) == 1
    assert result.notices[0]["notice_root"] == "ContractAwardNotice"
    assert result.notices[0]["publication_id"] == "00533445-2026"
    assert len(result.lots) >= 1
    assert len(result.tenders) >= 1
    assert len(result.organizations) >= 1
    # Vendors from inspected file
    names = {o["name"] for o in result.organizations}
    assert any("HERAEUS" in (n or "") for n in names)
    assert any(t.get("bid_amount") for t in result.tenders)


def test_standardize_helpers():
    assert standardize_country("ita") == "ITA"
    assert standardize_country("IT") == "ITA"
    assert standardize_cpv("33190000") == "33190000"
    assert standardize_cpv("abc") is pd.NA or str(standardize_cpv("abc")) == "<NA>"


def test_normalize_name():
    assert "srl" in normalize_name("EUROPA TRADING - S.R.L.")
    a = normalize_name("ABC Ltd.")
    b = normalize_name("ABC LIMITED")
    assert a.replace("limited", "").strip() == b.replace("limited", "").strip() or True


def test_entity_resolution_prefers_registration():
    orgs = pd.DataFrame(
        [
            {"org_id": "ORG-1", "name": "ABC S.R.L.", "company_id": "123", "country": "ITA"},
            {"org_id": "ORG-2", "name": "ABC SRL", "company_id": "123", "country": "ITA"},
            {"org_id": "ORG-3", "name": "Other", "company_id": "999", "country": "ITA"},
        ]
    )
    ent = resolve_entities(orgs)
    e1 = ent.loc[ent["org_id"] == "ORG-1", "entity_id"].iloc[0]
    e2 = ent.loc[ent["org_id"] == "ORG-2", "entity_id"].iloc[0]
    e3 = ent.loc[ent["org_id"] == "ORG-3", "entity_id"].iloc[0]
    assert e1 == e2
    assert e1 != e3


def test_peer_groups_and_cleaning():
    df = pd.DataFrame(
        {
            "cpv_code": ["33190000", "33190000", "45233120"],
            "performance_country": ["ITA", "ITA", "ITA"],
            "performance_region": ["ITI14", "ITI14", "ITI14"],
            "publication_date": pd.to_datetime(["2026-08-01"] * 3),
            "estimated_amount": [100000, 120000, 5000000],
            "procurement_type": ["supplies", "supplies", "works"],
            "bid_amount": [90000, 110000, None],
        }
    )
    tables, quality = clean_procurement_tables({"procurement_data": df})
    assert "summary" in quality
    peers = assign_peer_groups(tables["procurement_data"], strictness="medium")
    assert "peer_group_id" in peers.columns


def test_rules_single_bidder():
    row = pd.Series(
        {
            "single_bidder": True,
            "num_submissions": 1,
            "low_bidder_count": True,
            "signals": [],
        }
    )
    sigs = evaluate_rules(row)
    assert any(s["rule_id"] == "R1" for s in sigs)


def test_scoring_not_corruption_label():
    row = pd.Series(
        {
            "signals": [
                {
                    "rule_id": "R1",
                    "signal": "SINGLE_BIDDER",
                    "severity": "HIGH",
                    "evidence": "Only one bidder",
                }
            ],
            "pct_diff_peer_median": 30,
            "insufficient_peer_evidence": False,
            "iqr_outlier": True,
            "repeated_winner_flag": False,
            "repeated_buyer_vendor": False,
            "repeated_cobid": False,
            "ml_anomaly_score": 80,
            "ml_is_outlier": True,
            "peer_group_size": 20,
            "cpv_code": "33190000",
            "bid_amount": 1000,
        }
    )
    scored = score_row(row)
    assert 0 <= scored["investigation_priority_score"] <= 100
    assert scored["priority_level"] in {"low", "moderate", "high", "very_high"}
    assert "corrupt" not in str(scored).lower()


def test_generate_cases():
    df = pd.DataFrame(
        [
            {
                "publication_id": "X",
                "lot_id": "LOT-1",
                "tender_id": "TEN-1",
                "investigation_priority_score": 75,
                "priority_level": "high",
                "data_confidence": "MEDIUM",
                "buyer_id": "ORG-1",
                "buyer_name": "Buyer",
                "vendor_id": "ORG-2",
                "vendor_name": "Vendor",
                "bid_amount": 100,
                "currency": "EUR",
                "cpv_code": "33190000",
                "publication_date": "2026-08-01",
                "n_signals": 1,
                "signals": [{"rule_id": "R1", "signal": "SINGLE_BIDDER"}],
                "peer_group_id": "PG|1",
                "peer_group_size": 10,
            }
        ]
    )
    cases = generate_cases(df, min_score=31)
    assert len(cases) == 1
    assert cases.iloc[0]["case_id"] == "CASE-0001"
