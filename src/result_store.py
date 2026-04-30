from pathlib import Path

import pandas as pd

CUMULATIVE_CSV_PATH: Path = Path.home() / ".job_finder" / "results.csv"

REQUIRED_COLUMNS: list[str] = [
    "job_url",
    "title",
    "company",
    "location",
    "date_posted",
    "match_score",
    "reasoning",
    "match_tier",
    "run_timestamp",
]


def merge_results(new_batch: pd.DataFrame, csv_path: Path = CUMULATIVE_CSV_PATH) -> int:
    """Append new_batch to the cumulative CSV, deduplicating by job_url.

    Keeps the row with the higher match_score on conflict.
    Creates the file with a header row if it does not exist.
    Returns the count of net-new rows added.
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    if not csv_path.exists():
        new_batch.to_csv(csv_path, index=False)
        return len(new_batch)

    existing = pd.read_csv(csv_path)
    original_urls = set(existing["job_url"]) if "job_url" in existing.columns else set()

    # Combine existing and new batch, then deduplicate keeping higher match_score
    combined = pd.concat([existing, new_batch], ignore_index=True)

    # Sort so higher match_score comes first, then drop duplicates keeping first
    combined["match_score"] = pd.to_numeric(combined["match_score"], errors="coerce").fillna(0)
    combined = combined.sort_values("match_score", ascending=False)
    combined = combined.drop_duplicates(subset=["job_url"], keep="first")

    combined.to_csv(csv_path, index=False)

    net_new = sum(url not in original_urls for url in combined["job_url"])
    return net_new
