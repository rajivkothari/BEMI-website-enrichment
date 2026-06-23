"""Command-line entry point for the BEMI website enrichment tool.

Usage:
    python -m src.cli --input input/sample_practices.csv --output output/enriched.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import io as table_io
from .config import Settings
from .enrich import enrich_table


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="bemi-enrich",
        description=(
            "Enrich a spreadsheet of businesses/practices with likely official "
            "websites using the Google Maps Platform Places API."
        ),
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        type=Path,
        help="Path to the input CSV/XLSX (practice_name, phone, city, state).",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        help="Path to write the enriched CSV/XLSX.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N rows (useful for testing).",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="Default region for phone parsing (overrides ENRICH_REGION).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the enrichment pipeline. Returns a process exit code."""
    args = build_parser().parse_args(argv)

    settings = Settings.from_env()
    if args.region:
        settings.region = args.region

    df = table_io.load_table(args.input)
    if args.limit is not None:
        df = df.head(args.limit)

    enriched = enrich_table(df, settings)
    out_path = table_io.write_table(enriched, args.output)

    print(f"Wrote {len(enriched)} rows to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
