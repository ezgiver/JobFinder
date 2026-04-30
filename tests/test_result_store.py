"""Unit tests for result_store.merge_results (Requirements 5.1–5.5)."""

import pandas as pd
import pytest

from src.result_store import REQUIRED_COLUMNS, merge_results


def make_batch(rows: list[dict]) -> pd.DataFrame:
    """Build a DataFrame with all required columns, filling missing ones with defaults."""
    defaults = {col: "" for col in REQUIRED_COLUMNS}
    defaults["match_score"] = 0
    records = [{**defaults, **row} for row in rows]
    return pd.DataFrame(records, columns=REQUIRED_COLUMNS)


# ---------------------------------------------------------------------------
# 5.2 – Create new CSV when none exists
# ---------------------------------------------------------------------------

def test_creates_csv_when_missing(tmp_path):
    csv_path = tmp_path / "results.csv"
    batch = make_batch([
        {"job_url": "https://example.com/job/1", "title": "Engineer", "match_score": 80},
        {"job_url": "https://example.com/job/2", "title": "Developer", "match_score": 70},
    ])

    count = merge_results(batch, csv_path=csv_path)

    assert csv_path.exists(), "CSV file should be created"
    df = pd.read_csv(csv_path)
    assert list(df.columns[:len(REQUIRED_COLUMNS)]) == REQUIRED_COLUMNS, "Header must match REQUIRED_COLUMNS"
    assert len(df) == 2
    assert count == 2


# ---------------------------------------------------------------------------
# 5.1 – Appending non-duplicate rows increments count correctly
# ---------------------------------------------------------------------------

def test_append_non_duplicates_increments_count(tmp_path):
    csv_path = tmp_path / "results.csv"
    first = make_batch([{"job_url": "https://example.com/job/1", "match_score": 60}])
    merge_results(first, csv_path=csv_path)

    second = make_batch([
        {"job_url": "https://example.com/job/2", "match_score": 55},
        {"job_url": "https://example.com/job/3", "match_score": 50},
    ])
    count = merge_results(second, csv_path=csv_path)

    assert count == 2
    df = pd.read_csv(csv_path)
    assert len(df) == 3


# ---------------------------------------------------------------------------
# 5.4 – Duplicate job_url with higher score in new batch replaces existing row
# ---------------------------------------------------------------------------

def test_higher_score_in_new_batch_replaces_existing(tmp_path):
    csv_path = tmp_path / "results.csv"
    existing = make_batch([{"job_url": "https://example.com/job/1", "title": "Old", "match_score": 50}])
    merge_results(existing, csv_path=csv_path)

    new_batch = make_batch([{"job_url": "https://example.com/job/1", "title": "New", "match_score": 90}])
    merge_results(new_batch, csv_path=csv_path)

    df = pd.read_csv(csv_path)
    assert len(df) == 1, "Duplicate URL should not create a second row"
    assert df.iloc[0]["match_score"] == 90
    assert df.iloc[0]["title"] == "New"


# ---------------------------------------------------------------------------
# 5.4 – Duplicate job_url with lower score in new batch keeps existing row
# ---------------------------------------------------------------------------

def test_lower_score_in_new_batch_keeps_existing(tmp_path):
    csv_path = tmp_path / "results.csv"
    existing = make_batch([{"job_url": "https://example.com/job/1", "title": "Original", "match_score": 80}])
    merge_results(existing, csv_path=csv_path)

    new_batch = make_batch([{"job_url": "https://example.com/job/1", "title": "Worse", "match_score": 40}])
    merge_results(new_batch, csv_path=csv_path)

    df = pd.read_csv(csv_path)
    assert len(df) == 1
    assert df.iloc[0]["match_score"] == 80
    assert df.iloc[0]["title"] == "Original"


# ---------------------------------------------------------------------------
# 5.5 – Idempotent merge: merging the same batch twice == merging once
# ---------------------------------------------------------------------------

def test_idempotent_merge(tmp_path):
    """Validates: Requirements 5.5"""
    csv_path_once = tmp_path / "once.csv"
    csv_path_twice = tmp_path / "twice.csv"

    batch = make_batch([
        {"job_url": "https://example.com/job/1", "match_score": 75},
        {"job_url": "https://example.com/job/2", "match_score": 60},
    ])

    # Merge once
    merge_results(batch, csv_path=csv_path_once)

    # Merge twice
    merge_results(batch, csv_path=csv_path_twice)
    merge_results(batch, csv_path=csv_path_twice)

    df_once = pd.read_csv(csv_path_once).sort_values("job_url").reset_index(drop=True)
    df_twice = pd.read_csv(csv_path_twice).sort_values("job_url").reset_index(drop=True)

    pd.testing.assert_frame_equal(df_once, df_twice)
