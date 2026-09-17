# Government Procurement Auditing System

AI-assisted investigation support for government procurement data (TED eForms prototype).

**This system does not label vendors, buyers, or tenders as corrupt or fraudulent.**
It surfaces unusual patterns, combines evidence signals, assigns an **investigation-priority score**, and explains why each case was flagged so humans can decide where to look first.

## Architecture

```
TED / future India sources
        ↓
   Common schema (CSV / SQLite)
        ↓
Peer context → rules + stats + network + ML
        ↓
Evidence fusion → cases + explanations
        ↓
Streamlit dashboard / FastAPI
```

## Project layout

```
procurement_audit/
├── config.py              # thresholds & paths (no hard-coded magic in modules)
├── main.py                # CLI pipeline
├── requirements.txt
├── data/
│   ├── raw/               # first-pass extracts
│   ├── processed/         # cleaned analytical tables
│   └── output/            # cases, network.html, quality report
├── src/
│   ├── parser.py          # eForms/UBL XML → relational tables
│   ├── cleaner.py
│   ├── entity_resolution.py
│   ├── context_engine.py  # peer groups
│   ├── price_analysis.py
│   ├── bidder_analysis.py
│   ├── vendor_analysis.py
│   ├── network_analysis.py
│   ├── rules.py
│   ├── ml_anomaly.py
│   ├── scoring.py
│   ├── explanations.py
│   ├── database.py
│   └── pipeline.py
├── dashboard/app.py       # Streamlit
├── api/main.py            # FastAPI
└── tests/
```

## Quick start

```bash
cd procurement_audit
pip install -r requirements.txt

# Demo on a sample of XML files
python main.py --max-files 500

# Full August bulk folder (extracted XML + remaining tar.gz)
python main.py --source "C:\Users\anupa\Desktop\Coding\Python\M#\2026-08\08"

# Dashboard
streamlit run dashboard/app.py

# API
uvicorn api.main:app --reload
```

## Data source

TED daily bulk files under `2026-08\08\`, e.g. `20260803_2026147.tar.gz`.

Inspected notice roots in this dataset:

- `ContractAwardNotice` (awards, bids, winners)
- `ContractNotice` (calls for tender)
- `PriorInformationNotice`

Key tags used by the parser (real eForms/UBL):

- `cbc:ID`, `cbc:ContractFolderID`, `efbc:NoticePublicationID`, `efbc:PublicationDate`
- `cac:ContractingParty` → buyer `ORG-*`
- `efac:Organization` / `efac:Company` → names, `cbc:CompanyID`, NUTS, country
- `cac:ProcurementProjectLot` → lots, CPV (`cbc:ItemClassificationCode`), estimates
- `efac:LotResult` / `efac:ReceivedSubmissionsStatistics` → bidder counts (`t-esubm`)
- `efac:LotTender` → `cbc:RankCode`, `cbc:PayableAmount`, tendering party, lot link
- `efac:TenderingParty` / `efac:Tenderer` → vendor ORG links
- `efac:SettledContract` → `cbc:AwardDate`

## Outputs

| File | Purpose |
|------|---------|
| `data/processed/notices.csv` | Notice-level |
| `data/processed/lots.csv` | Lots |
| `data/processed/tenders.csv` | Bids/awards |
| `data/processed/organizations.csv` | Orgs |
| `data/processed/relationships.csv` | Edges |
| `data/processed/procurement_data.csv` | Analytical fact table + scores |
| `data/output/cases.csv` | Investigation cases |
| `data/output/network.html` | Interactive graph |
| `data/procurement_audit.db` | SQLite |

## Investigation priority bands

| Score | Level |
|------:|-------|
| 0–30 | low |
| 31–60 | moderate |
| 61–80 | high |
| 81–100 | very high |

These are **review priority** categories, not guilt probabilities.

Each case also has a separate **data confidence** (`HIGH` / `MEDIUM` / `LOW`) that is reduced when peer groups are tiny or markets are highly specialized.

## Tests

```bash
pytest tests/ -q
```

## India / alternate sources

Keep detection independent of TED: replace `src/parser.py` (or add `parsers/india.py`) that maps into the same columns in `procurement_data`. The peer engine, rules, scoring, and dashboard stay unchanged.

## Fields not available in TED prototype

Shared directors/ownership and payment trails are **not** invented. `relationships` is structured so company-registry joins can be added later with `relationship_type` + `confidence`.

## Privacy

Telephone and email are parsed into `organizations` but the dashboard does not display them by default (`HIDE_CONTACT_FIELDS_BY_DEFAULT` in `config.py`).
