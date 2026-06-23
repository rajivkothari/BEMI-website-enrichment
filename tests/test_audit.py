"""Tests for src.audit."""
import json
from datetime import datetime

import pandas as pd

from src import audit


def sample_df():
    return pd.DataFrame([
        {"practice_name": "A", "phone": "4155550001", "city": "San Francisco", "state": "CA",
         "google_place_id": "PID_A", "google_website": "https://a.example", "match_score": 92,
         "match_confidence": "high", "match_reason": "strong", "needs_review": False, "error": ""},
        {"practice_name": "B", "phone": "", "city": "Los Angeles", "state": "CA",
         "google_place_id": "PID_B", "google_website": "https://b.example", "match_score": 70,
         "match_confidence": "medium", "match_reason": "ok", "needs_review": True, "error": ""},
        {"practice_name": "C", "phone": "", "city": "Reno", "state": "NV",
         "google_place_id": "PID_C", "google_website": "", "match_score": 40,
         "match_confidence": "low", "match_reason": "weak", "needs_review": True, "error": ""},
        {"practice_name": "D", "phone": "", "city": "", "state": "",
         "google_place_id": "", "google_website": "", "match_score": "",
         "match_confidence": "", "match_reason": "", "needs_review": True, "error": "places_error: boom"},
    ])


T0 = datetime(2026, 6, 23, 4, 0, 0)
T1 = datetime(2026, 6, 23, 4, 1, 30)


class TestSummarize:
    def test_counts(self):
        log = audit.summarize_run(
            sample_df(), input_file="in.csv", output_file="out.csv",
            started_at=T0, completed_at=T1, flags={"limit": None, "no_cache": False},
            api_calls=5, cache_hits=2, cache_misses=5,
        )
        assert log["row_count"] == 4
        assert log["enriched_count"] == 3            # rows with a place_id
        assert log["high_confidence_count"] == 1
        assert log["medium_confidence_count"] == 1
        assert log["low_confidence_count"] == 1
        assert log["needs_review_count"] == 3
        assert log["errors_count"] == 1
        assert log["api_calls_estimated"] == 5
        assert log["cache_hits"] == 2
        assert log["cache_misses"] == 5
        assert log["started_at"] == "2026-06-23T04:00:00"
        assert log["completed_at"] == "2026-06-23T04:01:30"
        assert log["input_file"] == "in.csv"
        assert log["output_file"] == "out.csv"
        assert log["flags"] == {"limit": None, "no_cache": False}

    def test_is_json_serializable(self):
        log = audit.summarize_run(
            sample_df(), input_file="in.csv", output_file=None,
            started_at=T0, completed_at=T1, flags={}, api_calls=0, cache_hits=0, cache_misses=0,
        )
        json.dumps(log)  # must not raise
        assert log["output_file"] is None


class TestEvents:
    def test_columns_and_values(self):
        ev = audit.events_frame(sample_df())
        assert list(ev.columns) == audit.EVENT_COLUMNS
        assert list(ev["row_index"]) == [1, 2, 3, 4]
        assert ev.iloc[0]["selected_place_id"] == "PID_A"
        assert ev.iloc[0]["selected_website"] == "https://a.example"
        assert ev.iloc[0]["confidence"] == "high"
        assert ev.iloc[3]["error"] == "places_error: boom"


class TestWriteAudit:
    def test_writes_both_files(self, tmp_path):
        run_log_path, events_path, log = audit.write_audit(
            sample_df(), input_file="in.csv", output_file="out.csv",
            started_at=T0, completed_at=T1, flags={}, api_calls=1, cache_hits=0, cache_misses=1,
            output_dir=tmp_path, timestamp="20260623_040000",
        )
        assert run_log_path.name == "run_log_20260623_040000.json"
        assert events_path.name == "enrichment_events.csv"

        data = json.loads(run_log_path.read_text())
        assert data["row_count"] == 4 and data["errors_count"] == 1

        events = pd.read_csv(events_path, keep_default_na=False)
        assert list(events.columns) == audit.EVENT_COLUMNS
        assert len(events) == 4

    def test_default_timestamp_from_started_at(self, tmp_path):
        path, _, _ = audit.write_audit(
            sample_df(), input_file="i", output_file=None,
            started_at=datetime(2026, 6, 23, 4, 5, 6), completed_at=T1,
            flags={}, api_calls=0, cache_hits=0, cache_misses=0, output_dir=tmp_path,
        )
        assert path.name == "run_log_20260623_040506.json"
