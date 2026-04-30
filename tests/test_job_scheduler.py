"""
tests/test_job_scheduler.py — Unit and property-based tests for src/job_scheduler.py.

Covers tasks 5.4–5.5 of the multi-user-scheduled-delivery spec.

All DB interactions use in-memory SQLite via init_db(":memory:").
External dependencies (jobspy, google-genai, email_sender, sponsors) are
mocked so the tests run without network access or API keys.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date
from typing import Generator
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

import src.database as _db
from src.database import (
    create_user,
    get_session,
    get_user_results,
    init_db,
    save_job_result,
    set_schedule_enabled,
    upsert_profile,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash(password: str) -> str:
    try:
        from src.auth import hash_password

        return hash_password(password)
    except ImportError:
        return "hashed_" + password


@contextmanager
def in_memory_session() -> Generator[Session, None, None]:
    """Yield a fresh in-memory SQLite session for one test."""
    init_db(":memory:")
    session: Session = _db._SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _make_user(session: Session, email: str = "test@example.com"):
    return create_user(email, _hash("password123"), session)


def _make_complete_profile(session: Session, user_id: int):
    return upsert_profile(
        user_id=user_id,
        target_role="Software Engineer",
        cv_text="Experienced developer.",
        recipient_email="recipient@example.com",
        session=session,
    )


def _minimal_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "title": ["Engineer"],
            "company": ["Acme"],
            "location": ["Remote"],
            "match_score": [85],
        }
    )


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_scheduler_cache():
    """Clear st.cache_resource between tests so get_scheduler() is not reused."""
    try:
        from src.job_scheduler import get_scheduler

        get_scheduler.clear()
    except Exception:
        pass
    yield
    try:
        from src.job_scheduler import get_scheduler

        get_scheduler.clear()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Task 5.4 — Unit tests
# ---------------------------------------------------------------------------


class TestRunDailyDigestEmailBehaviour:
    """run_daily_digest sends email only when pipeline returns non-empty results."""

    def _run_digest_with_mock_pipeline(
        self,
        pipeline_return: pd.DataFrame,
        mocker,
        n_users: int = 1,
    ) -> MagicMock:
        """
        Set up an in-memory DB with ``n_users`` opted-in users, patch
        ``_run_pipeline_for_user`` to return ``pipeline_return``, patch
        ``send_results_email``, and call ``run_daily_digest``.

        Returns the mock for ``send_results_email``.
        """
        init_db(":memory:")
        session: Session = _db._SessionFactory()
        try:
            for i in range(n_users):
                user = create_user(f"user{i}@test.com", _hash("password123"), session)
                _make_complete_profile(session, user.id)
                set_schedule_enabled(user.id, True, session)
            session.commit()
        finally:
            session.close()

        mock_pipeline = mocker.patch(
            "src.job_scheduler._run_pipeline_for_user",
            return_value=pipeline_return,
        )
        mock_email = mocker.patch("src.job_scheduler.send_results_email")
        mock_save = mocker.patch("src.job_scheduler.save_job_result")

        from src.job_scheduler import run_daily_digest

        run_daily_digest()

        return mock_email, mock_pipeline, mock_save

    def test_email_sent_when_pipeline_returns_results(self, mocker):
        """run_daily_digest calls send_results_email when pipeline returns non-empty df."""
        mock_email, _, _ = self._run_digest_with_mock_pipeline(
            _minimal_df(), mocker, n_users=1
        )
        mock_email.assert_called_once()

    def test_email_not_sent_when_pipeline_returns_empty(self, mocker):
        """run_daily_digest does NOT call send_results_email when pipeline returns empty df."""
        mock_email, _, _ = self._run_digest_with_mock_pipeline(
            _empty_df(), mocker, n_users=1
        )
        mock_email.assert_not_called()

    def test_email_sent_for_each_user_with_results(self, mocker):
        """run_daily_digest calls send_results_email once per user with non-empty results."""
        mock_email, _, _ = self._run_digest_with_mock_pipeline(
            _minimal_df(), mocker, n_users=3
        )
        assert mock_email.call_count == 3

    def test_results_saved_when_non_empty(self, mocker):
        """run_daily_digest calls save_job_result when pipeline returns non-empty df."""
        _, _, mock_save = self._run_digest_with_mock_pipeline(
            _minimal_df(), mocker, n_users=1
        )
        mock_save.assert_called_once()

    def test_results_not_saved_when_empty(self, mocker):
        """run_daily_digest does NOT call save_job_result when pipeline returns empty df."""
        _, _, mock_save = self._run_digest_with_mock_pipeline(
            _empty_df(), mocker, n_users=1
        )
        mock_save.assert_not_called()


class TestRunDailyDigestLogging:
    """run_daily_digest logs a summary line after completing a run."""

    def test_summary_logged_after_run(self, mocker, caplog):
        """A summary line containing counts is logged at INFO level."""
        init_db(":memory:")
        session: Session = _db._SessionFactory()
        try:
            user = create_user("log@test.com", _hash("password123"), session)
            _make_complete_profile(session, user.id)
            set_schedule_enabled(user.id, True, session)
            session.commit()
        finally:
            session.close()

        mocker.patch(
            "src.job_scheduler._run_pipeline_for_user",
            return_value=_minimal_df(),
        )
        mocker.patch("src.job_scheduler.send_results_email")
        mocker.patch("src.job_scheduler.save_job_result")

        from src.job_scheduler import run_daily_digest

        with caplog.at_level(logging.INFO, logger="src.job_scheduler"):
            run_daily_digest()

        # The summary line must mention the key counts
        summary_lines = [r.message for r in caplog.records if "Daily digest complete" in r.message]
        assert summary_lines, "Expected a 'Daily digest complete' summary log line"
        summary = summary_lines[0]
        assert "users processed" in summary
        assert "emails sent" in summary
        assert "errors" in summary

    def test_summary_logged_with_no_users(self, mocker, caplog):
        """Summary is still logged even when there are no opted-in users."""
        init_db(":memory:")  # empty DB — no users

        from src.job_scheduler import run_daily_digest

        with caplog.at_level(logging.INFO, logger="src.job_scheduler"):
            run_daily_digest()

        summary_lines = [r.message for r in caplog.records if "Daily digest complete" in r.message]
        assert summary_lines, "Expected a summary log line even with zero users"


class TestGetScheduler:
    """get_scheduler() returns the same object on repeated calls (cache_resource)."""

    def test_get_scheduler_returns_scheduler(self):
        """get_scheduler() returns a BackgroundScheduler instance."""
        from apscheduler.schedulers.background import BackgroundScheduler

        from src.job_scheduler import get_scheduler

        scheduler = get_scheduler()
        assert isinstance(scheduler, BackgroundScheduler)
        scheduler.shutdown(wait=False)

    def test_get_scheduler_called_twice_returns_same_object(self):
        """get_scheduler() called twice returns the same cached object."""
        from src.job_scheduler import get_scheduler

        s1 = get_scheduler()
        s2 = get_scheduler()
        assert s1 is s2
        s1.shutdown(wait=False)

    def test_scheduler_has_daily_digest_job(self):
        """The scheduler has a job with id 'daily_digest'."""
        from src.job_scheduler import get_scheduler

        scheduler = get_scheduler()
        job_ids = [job.id for job in scheduler.get_jobs()]
        assert "daily_digest" in job_ids
        scheduler.shutdown(wait=False)

    def test_scheduler_is_running_after_get(self):
        """The scheduler is in the running state after get_scheduler() returns."""
        from src.job_scheduler import get_scheduler

        scheduler = get_scheduler()
        assert scheduler.running
        scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Task 5.5 — Property 8: Per-user error isolation in scheduler
# Validates: Requirements 5.7, 5.8
# ---------------------------------------------------------------------------


def _run_digest_with_flags(should_fail_flags: list[bool], mocker) -> int:
    """
    Set up an in-memory DB with one opted-in user per flag, patch
    ``_run_pipeline_for_user`` to raise for failing users and return a
    non-empty DataFrame for succeeding users, then call ``run_daily_digest``.

    Returns the count of users for whom ``send_results_email`` was called
    (i.e. successfully processed users).
    """
    init_db(":memory:")
    session: Session = _db._SessionFactory()
    user_ids: list[int] = []
    try:
        for i, _ in enumerate(should_fail_flags):
            user = create_user(f"iso{i}@test.com", _hash("password123"), session)
            _make_complete_profile(session, user.id)
            set_schedule_enabled(user.id, True, session)
            user_ids.append(user.id)
        session.commit()
    finally:
        session.close()

    call_count = 0

    def _side_effect(profile):
        nonlocal call_count
        idx = user_ids.index(profile.user_id)
        call_count += 1
        if should_fail_flags[idx]:
            raise RuntimeError(f"Simulated pipeline failure for user_id={profile.user_id}")
        return _minimal_df()

    mocker.patch("src.job_scheduler._run_pipeline_for_user", side_effect=_side_effect)
    mock_email = mocker.patch("src.job_scheduler.send_results_email")
    mocker.patch("src.job_scheduler.save_job_result")

    from src.job_scheduler import run_daily_digest

    run_daily_digest()

    return mock_email.call_count


@given(st.lists(st.booleans(), min_size=2, max_size=10))
@settings(max_examples=100, deadline=None)
def test_scheduler_per_user_error_isolation(should_fail_flags: list[bool]):
    """Property 8: Per-user error isolation in scheduler.

    For any list of opted-in users where some raise exceptions during pipeline
    execution, run_daily_digest() must still process all non-failing users.
    The count of successfully processed users must equal the total count minus
    the count of users that raised exceptions.

    **Validates: Requirements 5.7, 5.8**
    """
    from unittest.mock import patch

    init_db(":memory:")
    session: Session = _db._SessionFactory()
    user_ids: list[int] = []
    try:
        for i, _ in enumerate(should_fail_flags):
            user = create_user(f"prop8_{i}@test.com", _hash("password123"), session)
            _make_complete_profile(session, user.id)
            set_schedule_enabled(user.id, True, session)
            user_ids.append(user.id)
        session.commit()
    finally:
        session.close()

    def _side_effect(profile):
        idx = user_ids.index(profile.user_id)
        if should_fail_flags[idx]:
            raise RuntimeError(f"Simulated failure for user_id={profile.user_id}")
        return _minimal_df()

    email_call_count = 0

    def _mock_email(*args, **kwargs):
        nonlocal email_call_count
        email_call_count += 1

    with (
        patch("src.job_scheduler._run_pipeline_for_user", side_effect=_side_effect),
        patch("src.job_scheduler.send_results_email", side_effect=_mock_email),
        patch("src.job_scheduler.save_job_result"),
    ):
        from src.job_scheduler import run_daily_digest

        run_daily_digest()

    expected_success = sum(1 for f in should_fail_flags if not f)
    assert email_call_count == expected_success, (
        f"Expected {expected_success} successful user(s), "
        f"but send_results_email was called {email_call_count} time(s). "
        f"Flags: {should_fail_flags}"
    )
