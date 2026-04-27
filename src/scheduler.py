"""Headless scheduler for autonomous job-search pipeline execution."""

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from google import genai

from src.config_store import load_config, save_config
from src.cv_parser import extract_cv_text_from_path
from src.email_sender import send_results_email
from src.result_store import CUMULATIVE_CSV_PATH, merge_results
from src.scoring import score_jobs
from src.setup_prompt import run_setup
from src.sponsors import load_sponsor_register, verify_sponsors
from src.tiers import assign_tier

LOG_PATH = Path.home() / ".job_finder" / "scheduler.log"


def _ts() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_pipeline(config: dict) -> pd.DataFrame:
    """Execute scrape → sponsor-verify → score pipeline.

    Returns scored DataFrame. Prints timestamped stage status lines to stdout.
    Exits with status 1 if GEMINI_API_KEY is not set.
    """
    # Requirement 4.4: check for API key before doing anything
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(
            "ERROR: GEMINI_API_KEY environment variable is not set. "
            "Please export your Gemini API key before running the scheduler.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    target_role = config["target_role"]
    interval_days = config["run_interval_days"]

    # Stage 1: Scrape jobs
    try:
        import jobspy

        jobs_df = jobspy.scrape_jobs(
            site_name=["indeed", "linkedin"],
            search_term=target_role,
            location="London",
            results_wanted=100,
            hours_old=interval_days * 24,
            linkedin_fetch_description=True,
        )
    except Exception as e:
        print(f"[{_ts()}] Scraping failed: {e}", file=sys.stderr)
        raise

    scraped_count = len(jobs_df) if jobs_df is not None else 0
    print(f"[{_ts()}] Stage 1 – Scraping complete: {scraped_count} jobs scraped.")

    # Requirement 4.5: handle zero results
    if scraped_count == 0:
        print(f"[{_ts()}] WARNING: No jobs found for '{target_role}' in London over the last {interval_days} day(s).")
        return pd.DataFrame()

    # Stage 2: Sponsor verification
    sponsors = load_sponsor_register()
    sponsor_results = verify_sponsors(jobs_df["company"], sponsors)
    jobs_df = jobs_df.copy()
    jobs_df["verified_sponsor"] = sponsor_results["verified_sponsor"]
    jobs_df["sponsor_match_score"] = sponsor_results["sponsor_match_score"]

    verified_df = jobs_df[jobs_df["verified_sponsor"]].copy()
    verified_count = len(verified_df)
    print(f"[{_ts()}] Stage 2 – Sponsor verification complete: {verified_count} sponsor-verified jobs.")

    if verified_count == 0:
        return verified_df

    # Stage 3: Score jobs
    cv_text = extract_cv_text_from_path(config["cv_path"])
    scored_df = score_jobs(cv_text, verified_df, client)
    scored_count = len(scored_df)
    print(f"[{_ts()}] Stage 3 – Scoring complete: {scored_count} jobs scored.")

    # Assign match tiers
    scored_df = scored_df.copy()
    scored_df["match_tier"] = scored_df["match_score"].apply(assign_tier)

    # Stamp the run timestamp
    scored_df["run_timestamp"] = _ts()

    return scored_df


def main() -> None:
    """Main entry point: load/setup config, run scheduler loop."""
    # Step 1: Load or create config (Requirement 2.2)
    config = load_config()
    if config is None:
        config = run_setup()
        save_config(config)

    while True:
        # Step 2: Check completion (Requirement 3.2)
        if config["completed_iterations"] == config["iteration_count"]:
            print(
                f"All {config['iteration_count']} iteration(s) complete. "
                "Job search scheduler has finished."
            )
            sys.exit(0)

        # Step 3: Parse next_run_timestamp and sleep if needed (Requirements 3.1, 3.3)
        ts = config["next_run_timestamp"]
        next_run = datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)

        while True:
            now = datetime.now(timezone.utc)
            remaining_seconds = (next_run - now).total_seconds()
            if remaining_seconds <= 0:
                break
            remaining_iters = config["iteration_count"] - config["completed_iterations"]
            print(
                f"Next run scheduled at {next_run.strftime('%Y-%m-%dT%H:%M:%SZ')} UTC. "
                f"Remaining iterations: {remaining_iters}."
            )
            sleep_secs = min(60, remaining_seconds)
            time.sleep(sleep_secs)

        # Step 4: Print run header (Requirement 3.4)
        current_iter = config["completed_iterations"] + 1
        total_iters = config["iteration_count"]
        interval_days = config["run_interval_days"]
        run_start = datetime.now(timezone.utc)
        date_from = (run_start - timedelta(days=interval_days)).strftime("%Y-%m-%d")
        date_to = run_start.strftime("%Y-%m-%d")
        print(
            f"[{_ts()}] Starting iteration {current_iter}/{total_iters} "
            f"(searching jobs from {date_from} to {date_to})."
        )

        # Step 5: Run pipeline with error handling (Requirement 6.2)
        try:
            result_df = run_pipeline(config)
        except SystemExit:
            raise
        except Exception:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            logging.basicConfig(
                filename=str(LOG_PATH),
                level=logging.ERROR,
                format="%(asctime)s %(levelname)s %(message)s",
            )
            logging.exception("Pipeline failed on iteration %d", current_iter)
            print(
                f"ERROR: Pipeline failed on iteration {current_iter}. "
                f"See {LOG_PATH} for details.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Step 6: Persist results and print summary (Requirements 5.x, 6.3)
        scraped_count = len(result_df) if result_df is not None else 0

        # Counts from pipeline stages are printed by run_pipeline itself.
        # Determine sponsor-filtered and scored counts from result_df.
        jobs_found = scraped_count  # run_pipeline already printed scrape count
        jobs_scored = scraped_count  # result_df only contains scored rows

        if result_df is not None and not result_df.empty:
            new_jobs_added = merge_results(result_df)
        else:
            new_jobs_added = 0

        # Total jobs in CSV
        try:
            total_in_csv = len(pd.read_csv(CUMULATIVE_CSV_PATH))
        except FileNotFoundError:
            total_in_csv = 0

        print(
            f"[{_ts()}] Run summary — "
            f"jobs scored: {jobs_scored}, "
            f"new jobs added: {new_jobs_added}, "
            f"total jobs in CSV: {total_in_csv}."
        )

        # Step 6b: Email delivery (Requirement 3)
        recipient = config.get("recipient_email")
        if recipient and result_df is not None and not result_df.empty:
            run_date = datetime.now(timezone.utc)
            summary = {
                "jobs_scored": jobs_scored,
                "new_jobs_added": new_jobs_added,
                "total_in_csv": total_in_csv,
            }
            try:
                send_results_email(result_df, run_date, summary, recipient)
                print(f"[{_ts()}] Email delivered to {recipient}.")
            except EnvironmentError as exc:
                print(f"[{_ts()}] WARNING: {exc} — skipping email delivery.", file=sys.stderr)
            except Exception:
                logging.exception("Email delivery failed on iteration %d", current_iter)
                print(
                    f"[{_ts()}] WARNING: Email delivery failed. See {LOG_PATH} for details.",
                    file=sys.stderr,
                )

        # Step 7: Update config (Requirement 2.3)
        config["completed_iterations"] += 1
        next_ts = datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)
        next_ts = next_ts + timedelta(days=interval_days)
        config["next_run_timestamp"] = next_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        save_config(config)

        # Step 8: Loop back to step 2 (handled by while True)


if __name__ == "__main__":
    main()
