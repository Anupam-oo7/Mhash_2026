"""CLI entrypoint for the Government Procurement Auditing System."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src.pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze TED eForms procurement data and surface investigation-priority "
            "cases. Does not label entities as corrupt."
        )
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=config.DEFAULT_TED_SOURCE,
        help="Directory containing TED .tar.gz and/or extracted XML folders",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Limit number of XML files (useful for demos)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run the pipeline and print a concise summary.",
    )
    parser.add_argument(
        "--skip-parse",
        action="store_true",
        help="Reuse existing data/processed CSVs",
    )
    parser.add_argument(
        "--peer-strictness",
        choices=["strict", "medium", "broad"],
        default=None,
        help="Override peer group strictness",
    )
    args = parser.parse_args()

    if args.peer_strictness:
        config.PEER_STRICTNESS = args.peer_strictness
    if args.max_files is not None:
        config.MAX_FILES = args.max_files

    if args.demo:
        print("Running procurement audit demo...")
        print(f"Source: {args.source}")
        print(f"File limit: {args.max_files}")

    tables = run_pipeline(
        source_dir=args.source,
        max_files=args.max_files,
        skip_parse=args.skip_parse,
    )

    if args.demo:
        proc = tables.get("procurement_data")
        cases = tables.get("cases")
        print("\nDemo summary:")
        if proc is not None and not proc.empty:
            print(f"  procurements analyzed: {len(proc)}")
        if cases is not None and not cases.empty:
            print(f"  cases generated: {len(cases)}")
            print(f"  highest priority: {cases['priority_score'].max() if 'priority_score' in cases.columns else 'n/a'}")
        print("  Use Streamlit: streamlit run procurement_audit/dashboard/app.py")


if __name__ == "__main__":
    main()
