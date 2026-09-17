"""End-to-end pipeline: parse → clean → analyze → score → cases."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd

# Allow running as script from project root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src.parser import EformsParser, ParseResult, iter_xml_paths, parse_path, results_to_dataframes
from src.cleaner import clean_procurement_tables, save_quality_report
from src.entity_resolution import resolve_entities
from src.context_engine import assign_peer_groups
from src.price_analysis import analyze_prices
from src.bidder_analysis import analyze_bidders
from src.vendor_analysis import (
    annotate_vendor_behavior,
    build_buyer_summary,
    build_vendor_history,
    build_vendor_summary,
)
from src.network_analysis import (
    annotate_network_signals,
    build_graph,
    build_relationship_edges,
    export_pyvis,
    graph_features,
)
from src.rules import apply_rules
from src.ml_anomaly import add_ml_anomaly_scores
from src.scoring import apply_scoring, generate_cases
from src.explanations import build_explanations
from src import database as db


def _ensure_dirs() -> None:
    for p in (config.RAW_DIR, config.PROCESSED_DIR, config.OUTPUT_DIR):
        p.mkdir(parents=True, exist_ok=True)


def process_source(
    source_dir: Path,
    max_files: Optional[int] = None,
    progress_every: int = 100,
    extract_archives: bool = True,
) -> tuple[ParseResult, dict[str, Any]]:
    parser = EformsParser()
    combined = ParseResult()
    stats = {
        "total_xml_seen": 0,
        "successfully_parsed": 0,
        "failed_files": 0,
        "skipped_duplicate_names": 0,
    }
    error_log = Path(config.ERROR_LOG_PATH)
    error_log.parent.mkdir(parents=True, exist_ok=True)
    # truncate
    error_log.write_text("", encoding="utf-8")

    seen_names: set[str] = set()
    t0 = time.time()

    for path in iter_xml_paths(source_dir, extract_archives=extract_archives):
        name = Path(str(path).split("::")[-1]).name
        if name in seen_names:
            stats["skipped_duplicate_names"] += 1
            continue
        seen_names.add(name)

        stats["total_xml_seen"] += 1
        if max_files is not None and stats["total_xml_seen"] > max_files:
            stats["total_xml_seen"] -= 1
            break

        result = parse_path(parser, path)
        if result.errors and not result.notices:
            stats["failed_files"] += 1
            with error_log.open("a", encoding="utf-8") as fh:
                for err in result.errors:
                    fh.write(json.dumps(err) + "\n")
        else:
            stats["successfully_parsed"] += 1
            if result.errors:
                with error_log.open("a", encoding="utf-8") as fh:
                    for err in result.errors:
                        fh.write(json.dumps(err) + "\n")
            combined.extend(result)

        if stats["total_xml_seen"] % progress_every == 0:
            print(f"Processed:\n{stats['total_xml_seen']} files")

    elapsed = time.time() - t0
    stats.update(
        {
            "total_notices": len(combined.notices),
            "total_lots": len(combined.lots),
            "total_tenders": len(combined.tenders),
            "total_organizations": len(combined.organizations),
            "total_relationships": len(combined.relationships),
            "elapsed_seconds": round(elapsed, 2),
        }
    )
    print("\n=== Parse summary ===")
    for k, v in stats.items():
        print(f"{k}: {v}")
    return combined, stats


def save_tables(tables: dict[str, pd.DataFrame], processed_dir: Path) -> None:
    processed_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        if df is None:
            continue
        path = processed_dir / f"{name}.csv"
        temp_path = path.with_suffix(".csv.tmp")
        df.to_csv(temp_path, index=False)
        temp_path.replace(path)
        # Also parquet when possible
        try:
            df.to_parquet(processed_dir / f"{name}.parquet", index=False)
        except Exception:
            pass


def run_pipeline(
    source_dir: Optional[Path] = None,
    max_files: Optional[int] = None,
    skip_parse: bool = False,
) -> dict[str, pd.DataFrame]:
    _ensure_dirs()
    source_dir = Path(source_dir or config.DEFAULT_TED_SOURCE)
    max_files = max_files if max_files is not None else config.MAX_FILES

    if skip_parse and (config.PROCESSED_DIR / "procurement_data.csv").exists():
        print("Loading existing processed CSVs...")
        tables = {}
        for name in (
            "notices",
            "lots",
            "tenders",
            "organizations",
            "relationships",
            "procurement_data",
        ):
            p = config.PROCESSED_DIR / f"{name}.csv"
            tables[name] = pd.read_csv(p) if p.exists() else pd.DataFrame()
    else:
        print(f"Parsing TED source: {source_dir}")
        combined, parse_stats = process_source(
            source_dir,
            max_files=max_files,
            progress_every=config.PROGRESS_EVERY,
            extract_archives=config.EXTRACT_ARCHIVES,
        )
        tables = results_to_dataframes(combined)
        # Save raw extracts
        save_tables(tables, config.RAW_DIR)
        with (config.OUTPUT_DIR / "parse_stats.json").open("w", encoding="utf-8") as fh:
            json.dump(parse_stats, fh, indent=2)

    print("Cleaning...")
    tables, quality = clean_procurement_tables(tables)
    save_quality_report(quality, config.OUTPUT_DIR / "data_quality.md")

    print("Entity resolution...")
    entity_df = resolve_entities(tables["organizations"])
    tables["entity_resolution"] = entity_df

    # Map entity_id onto procurement vendors/buyers when possible
    if not entity_df.empty and not tables["procurement_data"].empty:
        ent_map = (
            entity_df.dropna(subset=["org_id"])
            .drop_duplicates("org_id")
            .set_index("org_id")["entity_id"]
        )
        proc = tables["procurement_data"]
        proc["vendor_entity_id"] = proc.get("vendor_id").map(ent_map)
        proc["buyer_entity_id"] = proc.get("buyer_id").map(ent_map)
        tables["procurement_data"] = proc

    print("Peer groups...")
    proc = assign_peer_groups(tables["procurement_data"])

    print("Price analysis...")
    proc = analyze_prices(proc)

    print("Bidder analysis...")
    proc = analyze_bidders(proc, tables.get("tenders"))

    print("Vendor / buyer summaries...")
    vendor_summary = build_vendor_summary(proc)
    buyer_summary = build_buyer_summary(proc)
    vendor_history = build_vendor_history(proc)
    proc = annotate_vendor_behavior(proc, vendor_summary)

    print("Network...")
    edges = build_relationship_edges(tables.get("relationships", pd.DataFrame()), proc)
    G = build_graph(edges, tables.get("organizations"), proc)
    node_features = graph_features(G)
    proc = annotate_network_signals(proc, edges)
    net_path = export_pyvis(G, config.OUTPUT_DIR / "network.html")

    print("Rules...")
    proc, signals = apply_rules(proc, buyer_summary)

    print("ML anomaly...")
    proc = add_ml_anomaly_scores(proc)

    print("Scoring & cases...")
    proc = apply_scoring(proc)
    cases = generate_cases(proc)
    explanations = build_explanations(cases, proc)

    tables["procurement_data"] = proc
    tables["vendor_summary"] = vendor_summary
    tables["buyer_summary"] = buyer_summary
    tables["vendor_history"] = vendor_history
    tables["relationships_agg"] = edges
    tables["network_nodes"] = node_features
    tables["signals"] = signals
    tables["cases"] = cases
    tables["explanations"] = explanations
    tables["investigation_queue"] = cases.copy()

    # Peer analysis export
    peer_cols = [
        c
        for c in (
            "publication_id",
            "lot_id",
            "tender_id",
            "peer_group_id",
            "peer_group_size",
            "peer_median",
            "pct_diff_peer_median",
            "peer_percentile",
            "insufficient_peer_evidence",
            "price_ratio",
            "bid_amount",
            "estimated_amount",
        )
        if c in proc.columns
    ]
    tables["peer_analysis"] = proc[peer_cols].copy() if peer_cols else pd.DataFrame()

    print("Saving outputs...")
    save_tables(
        {
            k: tables[k]
            for k in (
                "notices",
                "lots",
                "tenders",
                "organizations",
                "relationships",
                "procurement_data",
                "entity_resolution",
                "vendor_summary",
                "buyer_summary",
                "vendor_history",
                "relationships_agg",
                "network_nodes",
                "signals",
                "cases",
                "explanations",
                "investigation_queue",
                "peer_analysis",
            )
            if k in tables
        },
        config.PROCESSED_DIR,
    )

    # Convenience copies in output/
    for name in ("cases", "signals", "investigation_queue", "vendor_summary", "buyer_summary"):
        if name in tables and tables[name] is not None:
            path = config.OUTPUT_DIR / f"{name}.csv"
            temp_path = path.with_suffix(".csv.tmp")
            tables[name].to_csv(temp_path, index=False)
            temp_path.replace(path)
    if not explanations.empty:
        path = config.OUTPUT_DIR / "case_explanations.csv"
        temp_path = path.with_suffix(".csv.tmp")
        explanations.to_csv(temp_path, index=False)
        temp_path.replace(path)

    print("Writing SQLite...")
    conn = db.connect()
    db.init_schema(conn)
    for name in (
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
        "entity_resolution",
    ):
        if name in tables:
            db.write_dataframe(conn, name, tables[name])
    conn.close()

    print(f"Network visualization: {net_path}")
    print(f"Cases: {len(cases)}")
    print("Pipeline complete.")
    return tables


if __name__ == "__main__":
    run_pipeline()
