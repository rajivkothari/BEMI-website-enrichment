"""Audit logging for an enrichment run.

Writes two artifacts per run:
  * ``run_log_{timestamp}.json`` - run-level metadata and summary counts.
  * ``enrichment_events.csv``    - one row per input row (latest run).

Counts are derived from the enriched DataFrame; API/cache figures come from
the client and cache counters.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

import pandas as pd

# Per-row event columns (order matters for the CSV).
EVENT_COLUMNS = [
    "row_index", "practice_name", "phone", "city", "state",
    "selected_place_id", "selected_website", "score", "confidence",
    "needs_review", "reason", "error",
]


def _is_true(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _count_nonempty(df: pd.DataFrame, column: str) -> int:
    if column not in df.columns:
        return 0
    return int((df[column].fillna("").astype(str).str.strip() != "").sum())


def _count_true(df: pd.DataFrame, column: str) -> int:
    if column not in df.columns:
        return 0
    return int(sum(_is_true(v) for v in df[column]))


def _confidence_count(df: pd.DataFrame, level: str) -> int:
    if "match_confidence" not in df.columns:
        return 0
    return int((df["match_confidence"].astype(str).str.strip().str.lower() == level).sum())


def summarize_run(
    df: pd.DataFrame,
    *,
    input_file: Any,
    output_file: Any,
    started_at: datetime,
    completed_at: datetime,
    flags: Mapping[str, Any],
    api_calls: int,
    cache_hits: int,
    cache_misses: int,
) -> dict:
    """Build the run-log dict from an enriched DataFrame and run counters."""
    return {
        "input_file": str(input_file),
        "output_file": str(output_file) if output_file is not None else None,
        "started_at": started_at.isoformat(timespec="seconds"),
        "completed_at": completed_at.isoformat(timespec="seconds"),
        "row_count": int(len(df)),
        # "enriched" = a Google place was selected for the row.
        "enriched_count": _count_nonempty(df, "google_place_id"),
        "high_confidence_count": _confidence_count(df, "high"),
        "medium_confidence_count": _confidence_count(df, "medium"),
        "low_confidence_count": _confidence_count(df, "low"),
        "needs_review_count": _count_true(df, "needs_review"),
        "errors_count": _count_nonempty(df, "error"),
        "api_calls_estimated": int(api_calls),
        "cache_hits": int(cache_hits),
        "cache_misses": int(cache_misses),
        "flags": dict(flags),
    }


def events_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Project the enriched DataFrame into the per-row events table."""
    df = df.reset_index(drop=True).fillna("")

    def col(name: str) -> pd.Series:
        if name in df.columns:
            return df[name]
        return pd.Series([""] * len(df))

    return pd.DataFrame({
        "row_index": range(1, len(df) + 1),
        "practice_name": col("practice_name"),
        "phone": col("phone"),
        "city": col("city"),
        "state": col("state"),
        "selected_place_id": col("google_place_id"),
        "selected_website": col("google_website"),
        "score": col("match_score"),
        "confidence": col("match_confidence"),
        "needs_review": col("needs_review"),
        "reason": col("match_reason"),
        "error": col("error"),
    }, columns=EVENT_COLUMNS)


def write_audit(
    df: pd.DataFrame,
    *,
    input_file: Any,
    output_file: Any,
    started_at: datetime,
    completed_at: datetime,
    flags: Mapping[str, Any],
    api_calls: int,
    cache_hits: int,
    cache_misses: int,
    output_dir: Any = "output",
    timestamp: Optional[str] = None,
) -> Tuple[Path, Path, dict]:
    """Write ``run_log_{ts}.json`` and ``enrichment_events.csv``.

    Returns ``(run_log_path, events_path, run_log_dict)``.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = timestamp or started_at.strftime("%Y%m%d_%H%M%S")

    run_log = summarize_run(
        df,
        input_file=input_file,
        output_file=output_file,
        started_at=started_at,
        completed_at=completed_at,
        flags=flags,
        api_calls=api_calls,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
    )
    run_log_path = out_dir / f"run_log_{ts}.json"
    run_log_path.write_text(json.dumps(run_log, indent=2), encoding="utf-8")

    events_path = out_dir / "enrichment_events.csv"
    events_frame(df).to_csv(events_path, index=False)

    return run_log_path, events_path, run_log
