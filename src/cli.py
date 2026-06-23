"""Command-line entry point for the BEMI website enrichment tool.

Usage:
    python -m src.cli input/sample_practices.csv --output output/enriched.csv
    python -m src.cli input/sample_practices.csv --dry-run --limit 5 --verbose
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from . import audit
from . import bullseye_export
from . import google_places
from . import io as table_io
from .cache import DEFAULT_TTL_DAYS, SQLiteCache
from .config import Settings
from .enrich import enrich_table

logger = logging.getLogger("bemi")


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
        "input",
        type=Path,
        help="Input CSV/XLSX with columns: practice_name, phone, city, state.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output CSV/XLSX path. Defaults to output/<input-stem>.enriched<ext>.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Normalize inputs and build queries but make no API calls and "
        "write no output (useful for validating input without spending quota).",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Skip the Place Details lookup (faster/cheaper; may miss some websites).",
    )
    parser.add_argument(
        "--verify-websites",
        action="store_true",
        help="Fetch each matched homepage and verify the phone/city/state on "
        "it for extra confidence (off by default; adds time).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose, per-row logging.",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="Default region for phone parsing (ISO 3166, e.g. US). Overrides ENRICH_REGION.",
    )
    parser.add_argument(
        "--cache-db",
        type=Path,
        default=Path(".cache/enrichment_cache.sqlite"),
        help="SQLite cache file for Places responses.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable the response cache (always call the API).",
    )
    parser.add_argument(
        "--cache-ttl-days",
        type=float,
        default=DEFAULT_TTL_DAYS,
        help="Treat cached entries older than this many days as misses.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from an existing output file: skip rows that already have "
        "a google_place_id or error.",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=50,
        help="Write partial output every N enriched rows (0 disables).",
    )
    parser.add_argument(
        "--bullseye-json",
        type=Path,
        default=None,
        help="Also write Bullseye-compatible enrichment payloads (JSON Lines) to this path.",
    )
    return parser


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # Keep third-party HTTP logs quiet even in verbose mode (also avoids any
    # chance of request internals showing up in our output).
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _default_output(input_path: Path) -> Path:
    """Derive a default output path under output/ preserving the extension."""
    suffix = input_path.suffix.lower() if input_path.suffix else ".csv"
    return Path("output") / f"{input_path.stem}.enriched{suffix}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the enrichment pipeline. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    started_at = datetime.now()

    settings = Settings.from_env()
    if args.region:
        settings.region = args.region

    # Load input.
    try:
        df = table_io.load_table(args.input)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Could not read input %s: %s", args.input, exc)
        return 2
    if args.limit is not None:
        df = df.head(args.limit)
    logger.info("Loaded %d rows from %s", len(df), args.input)

    out_path = args.output or _default_output(args.input)

    # Resume: load any existing output to skip already-enriched rows.
    prior = None
    if args.resume and not args.dry_run:
        if out_path.exists():
            try:
                prior = table_io.load_table(out_path)
                logger.info("Resume: loaded %d existing rows from %s", len(prior), out_path)
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("Resume: could not read %s (%s); starting fresh", out_path, exc)
        else:
            logger.info("Resume requested but %s does not exist; starting fresh", out_path)

    # Build the cache and Places client up front (unless this is a dry run) so
    # a missing key fails fast with a clear message instead of erroring rows.
    cache = None
    client = None
    if not args.dry_run:
        if not args.no_cache:
            cache = SQLiteCache(args.cache_db, ttl_days=args.cache_ttl_days)
            logger.info("Cache: %s (ttl %s days)", args.cache_db, args.cache_ttl_days)
        try:
            client = google_places.GooglePlacesClient(settings.api_key, cache=cache)
        except google_places.PlacesError as exc:
            logger.error("%s", exc)  # message does not contain the key
            if cache is not None:
                cache.close()
            return 2

    def _checkpoint(partial) -> None:
        table_io.write_table(partial, out_path)
        logger.info("Checkpoint: wrote %d rows to %s", len(partial), out_path)

    try:
        enriched = enrich_table(
            df,
            client=client,
            region=settings.region,
            fetch_details=not args.no_details,
            verify_websites=args.verify_websites,
            dry_run=args.dry_run,
            prior=prior,
            checkpoint_every=args.checkpoint_every,
            on_checkpoint=_checkpoint,
        )

        if args.dry_run:
            logger.info("Dry run complete; no output written.")
            return 0

        table_io.write_table(enriched, out_path)
        logger.info("Wrote %d rows to %s", len(enriched), out_path)

        flags = {
            "limit": args.limit,
            "no_details": args.no_details,
            "verify_websites": args.verify_websites,
            "no_cache": args.no_cache,
            "cache_db": str(args.cache_db),
            "cache_ttl_days": args.cache_ttl_days,
            "region": settings.region,
        }
        run_log_path, events_path, run_log = audit.write_audit(
            enriched,
            input_file=args.input,
            output_file=out_path,
            started_at=started_at,
            completed_at=datetime.now(),
            flags=flags,
            api_calls=client.api_call_count if client else 0,
            cache_hits=cache.hits if cache else 0,
            cache_misses=cache.misses if cache else 0,
            output_dir=out_path.parent,
        )
        logger.info("Audit log: %s | events: %s", run_log_path, events_path)
        logger.info(
            "Summary: %d/%d enriched, %d need review, %d errors; "
            "api_calls~%d, cache hits/misses %d/%d",
            run_log["enriched_count"], run_log["row_count"],
            run_log["needs_review_count"], run_log["errors_count"],
            run_log["api_calls_estimated"], run_log["cache_hits"], run_log["cache_misses"],
        )

        if args.bullseye_json is not None:
            bullseye_export.write_jsonl(enriched, args.bullseye_json)
            logger.info("Wrote %d Bullseye payloads to %s", len(enriched), args.bullseye_json)
        return 0
    finally:
        if cache is not None:
            cache.close()


if __name__ == "__main__":
    raise SystemExit(main())
