"""
job_scheduler.py — APScheduler background scheduler for the multi-user Job Finder.

Covers tasks 5.1–5.3 of the multi-user-scheduled-delivery spec.

Public API
----------
get_scheduler()     — Create/start the BackgroundScheduler (cached via st.cache_resource).
run_daily_digest()  — Entry point called by APScheduler at 09:00 UTC daily.

Internal helper
---------------
_run_pipeline_for_user(profile) — Execute scrape → sponsor-verify → score for one user.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

# Load .env explicitly from the project root (parent of src/)
_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=True)

# Ensure scheduler log output reaches the terminal and a log file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scheduler.log"),
    ],
)

try:
    from src.database import get_scheduled_users, get_session, save_job_result
    from src.email_sender import send_results_email
    from src.scoring import score_jobs
    from src.sponsors import load_sponsor_register, verify_sponsors
except ModuleNotFoundError:
    from database import get_scheduled_users, get_session, save_job_result  # type: ignore[no-redef]
    from email_sender import send_results_email  # type: ignore[no-redef]
    from scoring import score_jobs  # type: ignore[no-redef]
    from sponsors import load_sponsor_register, verify_sponsors  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal pipeline helper  (Task 5.1)
# ---------------------------------------------------------------------------


_UK_KEYWORDS = {"uk", "united kingdom", "england", "scotland", "wales", "northern ireland",
                "london", "manchester", "birmingham", "edinburgh", "bristol", "leeds",
                "liverpool", "sheffield", "cambridge", "oxford", "glasgow"}


def _is_uk_location(location: str) -> bool:
    """Return True if the location string refers to a UK city/region."""
    return any(kw in location.lower() for kw in _UK_KEYWORDS)


def _run_pipeline_for_user(profile) -> pd.DataFrame:
    """Execute scrape → sponsor-verify (UK only) → score for a single user.

    UK locations: sponsor filter applied (only verified UK visa sponsors kept).
    International locations: all jobs included, no sponsor filter.

    Parameters
    ----------
    profile:
        A profile object with uk_location_list and international_location_list properties.

    Returns
    -------
    pd.DataFrame
        Scored DataFrame (may be empty if no jobs are found).

    Raises
    ------
    EnvironmentError
        If ``GEMINI_API_KEY`` is not set in the environment.
    RuntimeError
        If Gemini API rate limit is exceeded after retries.
    """
    # Always force-read .env to ensure the correct key value is used
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip("\"'")
                os.environ[k] = v

    api_key = os.environ.get("GEMINI_API_KEY", "").strip().strip("\"'")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")

    from google import genai
    from google.api_core import exceptions as google_exceptions

    client = genai.Client(api_key=api_key)

    import jobspy

    filtered_dfs = []
    sponsors = None  # loaded lazily only if UK locations exist

    # --- UK locations: sponsor filter applied ---
    uk_locations = getattr(profile, "uk_location_list", ["London"])
    for location in uk_locations:
        logger.info("🇬🇧 Scraping UK jobs in %r for user_id=%s...", location, profile.user_id)
        try:
            loc_df = jobspy.scrape_jobs(
                site_name=["indeed", "linkedin"],
                search_term=profile.target_role,
                location=location,
                results_wanted=100,
                hours_old=24,
                linkedin_fetch_description=True,
            )
        except Exception:
            logger.exception("Scraping failed for UK location=%r, user_id=%s.", location, profile.user_id)
            continue

        if loc_df is None or loc_df.empty:
            logger.info("No jobs scraped in %r.", location)
            continue

        if sponsors is None:
            sponsors = load_sponsor_register()
        sponsor_results = verify_sponsors(loc_df["company"], sponsors)
        loc_df = loc_df.copy()
        loc_df["verified_sponsor"] = sponsor_results["verified_sponsor"]
        loc_df["sponsor_match_score"] = sponsor_results["sponsor_match_score"]
        verified = loc_df[loc_df["verified_sponsor"]].copy()
        logger.info("%d sponsor-verified jobs in %r.", len(verified), location)
        if not verified.empty:
            filtered_dfs.append(verified)

    # --- International locations: no sponsor filter ---
    intl_locations = getattr(profile, "international_location_list", [])
    for location in intl_locations:
        logger.info("🌍 Scraping international jobs in %r for user_id=%s...", location, profile.user_id)
        try:
            loc_df = jobspy.scrape_jobs(
                site_name=["indeed", "linkedin"],
                search_term=profile.target_role,
                location=location,
                results_wanted=100,
                hours_old=24,
                linkedin_fetch_description=True,
            )
        except Exception:
            logger.exception("Scraping failed for international location=%r, user_id=%s.", location, profile.user_id)
            continue

        if loc_df is None or loc_df.empty:
            logger.info("No jobs scraped in %r.", location)
            continue

        loc_df = loc_df.copy()
        loc_df["verified_sponsor"] = False
        loc_df["sponsor_match_score"] = 0
        logger.info("%d jobs in %r (no sponsor filter).", len(loc_df), location)
        filtered_dfs.append(loc_df)

    if not filtered_dfs:
        logger.info("No jobs to score for user_id=%s.", profile.user_id)
        return pd.DataFrame()

    jobs_df = pd.concat(filtered_dfs, ignore_index=True).drop_duplicates(subset="job_url", keep="first")
    logger.info("Scoring %d total jobs for user_id=%s...", len(jobs_df), profile.user_id)

    # Score jobs with retry logic for rate limiting
    max_retries = 3
    retry_delay = 60  # seconds
    
    for attempt in range(max_retries):
        try:
            scored_df = score_jobs(profile.cv_text, jobs_df, client)
            return scored_df
        except google_exceptions.ResourceExhausted as e:
            if attempt < max_retries - 1:
                logger.warning(
                    "Gemini API rate limit hit for user_id=%s (attempt %d/%d). "
                    "Waiting %d seconds before retry...",
                    profile.user_id, attempt + 1, max_retries, retry_delay
                )
                import time
                time.sleep(retry_delay)
                retry_delay *= 2  # exponential backoff
            else:
                logger.error(
                    "Gemini API rate limit exceeded for user_id=%s after %d attempts.",
                    profile.user_id, max_retries
                )
                raise RuntimeError(
                    f"Gemini API rate limit exceeded after {max_retries} attempts. "
                    "Please try again later or reduce the number of jobs to score."
                ) from e
        except Exception as e:
            logger.exception("Unexpected error scoring jobs for user_id=%s", profile.user_id)
            raise
    
    # Should never reach here, but just in case
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Daily digest entry point  (Task 5.2)
# ---------------------------------------------------------------------------


def run_daily_digest() -> None:
    """Entry point called by APScheduler at 09:00 UTC.

    1. Opens a DB session.
    2. Queries all opted-in users via ``get_scheduled_users()``.
    3. For each user: runs the pipeline; if results are non-empty, saves them
       and sends a digest email.
    4. Catches and logs all exceptions per user without stopping the loop.
    5. Logs a final summary line.

    Requirements: 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9
    """
    users_processed = 0
    emails_sent = 0
    errors = 0

    with get_session() as session:
        profiles = get_scheduled_users(session)
        # Extract plain values immediately — avoids DetachedInstanceError
        # if SQLAlchemy expires objects after the session closes
        profile_data = [
            {
                "user_id": p.user_id,
                "target_role": p.target_role or "",
                "cv_text": p.cv_text or "",
                "recipient_email": p.recipient_email or "",
                "uk_locations": p.uk_locations or "London",
                "international_locations": p.international_locations or "",
            }
            for p in profiles
        ]

    logger.info("Daily digest started — %d opted-in user(s).", len(profile_data))

    run_date = datetime.now(timezone.utc)

    for pdata in profile_data:
        users_processed += 1

        # Build a plain namespace so _run_pipeline_for_user gets the fields it needs
        class _ProfileProxy:
            user_id = pdata["user_id"]
            target_role = pdata["target_role"]
            cv_text = pdata["cv_text"]
            recipient_email = pdata["recipient_email"]
            uk_locations = pdata["uk_locations"]
            international_locations = pdata["international_locations"]

            @property
            def uk_location_list(self):
                locs = pdata["uk_locations"]
                if locs and locs.strip():
                    return [l.strip() for l in locs.split(",") if l.strip()]
                return ["London"]

            @property
            def international_location_list(self):
                locs = pdata["international_locations"]
                if locs and locs.strip():
                    return [l.strip() for l in locs.split(",") if l.strip()]
                return []

        try:
            scored_df = _run_pipeline_for_user(_ProfileProxy())
        except RuntimeError as e:
            # Rate limiting or other runtime errors
            if "rate limit" in str(e).lower():
                logger.warning(
                    "Rate limit hit for user_id=%s — skipping this run. "
                    "User will be included in the next scheduled run.",
                    pdata["user_id"]
                )
            else:
                logger.error("Runtime error for user_id=%s: %s", pdata["user_id"], e)
            errors += 1
            continue
        except Exception:
            logger.exception("Pipeline failed for user_id=%s.", pdata["user_id"])
            errors += 1
            continue

        if scored_df.empty:
            logger.info(
                "No results for user_id=%s — skipping save and email.", pdata["user_id"]
            )
            continue

        # Persist results
        try:
            with get_session() as session:
                save_job_result(pdata["user_id"], run_date.date(), scored_df, session)
        except Exception:
            logger.exception(
                "Failed to save job results for user_id=%s.", pdata["user_id"]
            )
            errors += 1
            continue

        # Send digest email
        try:
            summary = {
                "jobs_scored": len(scored_df),
                "new_jobs_added": len(scored_df),
                "total_in_csv": len(scored_df),
            }
            send_results_email(
                df=scored_df,
                run_date=run_date,
                summary=summary,
                recipient=pdata["recipient_email"],
            )
            emails_sent += 1
            logger.info(
                "Digest email sent to %s for user_id=%s.",
                pdata["recipient_email"],
                pdata["user_id"],
            )
        except Exception:
            logger.exception(
                "Email delivery failed for user_id=%s.", pdata["user_id"]
            )
            errors += 1

    logger.info(
        "Daily digest complete — users processed: %d, emails sent: %d, errors: %d.",
        users_processed,
        emails_sent,
        errors,
    )


# ---------------------------------------------------------------------------
# Scheduler factory  (Task 5.3)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Scheduler factory  (Task 5.3)
# ---------------------------------------------------------------------------

# Module-level singleton — survives Streamlit reruns without relying on
# cache_resource, which can silently drop the background thread.
_scheduler: BackgroundScheduler | None = None


@st.cache_resource
def get_scheduler() -> BackgroundScheduler:
    """Create, configure, and start the APScheduler BackgroundScheduler.

    The cron trigger fires ``run_daily_digest`` at the configured UTC time daily.
    Uses both a module-level singleton and ``@st.cache_resource`` to ensure
    a single instance across Streamlit reruns.

    Returns
    -------
    BackgroundScheduler
        The running scheduler instance.

    Requirements: 5.1
    """
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_daily_digest,
        trigger="cron",
        hour=9,
        minute=0,
        id="daily_digest",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started with daily_digest job at 09:00 UTC.")
    return _scheduler
