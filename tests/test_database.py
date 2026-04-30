"""
tests/test_database.py — Unit and property-based tests for src/database.py.

Covers tasks 2.6–2.12 of the multi-user-scheduled-delivery spec.
All tests use in-memory SQLite via init_db(":memory:") for isolation.
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from datetime import date
from typing import Generator

import pandas as pd
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.database import (
    JobResult,
    User,
    UserProfile,
    _SessionFactory,
    create_user,
    get_profile,
    get_scheduled_users,
    get_user_by_email,
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
    """Minimal stand-in for hash_password until auth.py is implemented.

    Uses a simple prefix so the stored value is never equal to the plaintext.
    """
    try:
        from src.auth import hash_password  # type: ignore[import]

        return hash_password(password)
    except ImportError:
        return "hashed_" + password


@contextmanager
def in_memory_session() -> Generator[Session, None, None]:
    """Yield a fresh in-memory SQLite session for one test."""
    import src.database as _db

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


def _make_user(session: Session, email: str = "test@example.com") -> User:
    """Create and flush a user row; returns the User object."""
    user = create_user(email, _hash("password123"), session)
    return user


def _make_complete_profile(session: Session, user_id: int) -> UserProfile:
    """Upsert a fully-populated profile for user_id."""
    return upsert_profile(
        user_id=user_id,
        target_role="Software Engineer",
        cv_text="Experienced developer with Python skills.",
        recipient_email="recipient@example.com",
        session=session,
    )


def _minimal_df() -> pd.DataFrame:
    """Return a small DataFrame suitable for save_job_result."""
    return pd.DataFrame(
        {
            "title": ["Engineer"],
            "company": ["Acme"],
            "location": ["Remote"],
            "match_score": [0.9],
        }
    )


# ---------------------------------------------------------------------------
# Task 2.6 — Unit tests
# ---------------------------------------------------------------------------


class TestInitDb:
    def test_init_db_creates_tables(self):
        """init_db() creates the expected tables."""
        import src.database as _db

        init_db(":memory:")
        from sqlalchemy import inspect

        inspector = inspect(_db._engine)
        tables = inspector.get_table_names()
        assert "users" in tables
        assert "user_profiles" in tables
        assert "job_results" in tables

    def test_init_db_is_idempotent(self):
        """Calling init_db() twice raises no error and tables still exist."""
        import src.database as _db

        init_db(":memory:")
        init_db(":memory:")  # second call — must not raise
        from sqlalchemy import inspect

        inspector = inspect(_db._engine)
        tables = inspector.get_table_names()
        assert "users" in tables


class TestCreateUser:
    def test_create_user_returns_user_with_id(self):
        """create_user() returns a User with a populated id."""
        with in_memory_session() as session:
            user = _make_user(session)
            assert user.id is not None
            assert user.email == "test@example.com"

    def test_create_user_duplicate_email_raises_integrity_error(self):
        """create_user() raises IntegrityError on duplicate email."""
        import src.database as _db

        init_db(":memory:")
        session: Session = _db._SessionFactory()
        try:
            _make_user(session, email="dup@example.com")
            session.commit()
            with pytest.raises(IntegrityError):
                create_user("dup@example.com", _hash("anotherpass"), session)
        finally:
            session.rollback()
            session.close()


class TestGetUserByEmail:
    def test_get_user_by_email_returns_user(self):
        """get_user_by_email() returns the correct User."""
        with in_memory_session() as session:
            _make_user(session, email="find@example.com")
            found = get_user_by_email("find@example.com", session)
            assert found is not None
            assert found.email == "find@example.com"

    def test_get_user_by_email_returns_none_for_unknown(self):
        """get_user_by_email() returns None for an email that doesn't exist."""
        with in_memory_session() as session:
            result = get_user_by_email("nobody@example.com", session)
            assert result is None


class TestUpsertProfile:
    def test_upsert_profile_creates_new_row(self):
        """upsert_profile() inserts a new profile row."""
        with in_memory_session() as session:
            user = _make_user(session)
            profile = _make_complete_profile(session, user.id)
            assert profile.user_id == user.id
            assert profile.target_role == "Software Engineer"

    def test_upsert_profile_updates_existing_row(self):
        """upsert_profile() updates an existing profile row."""
        with in_memory_session() as session:
            user = _make_user(session)
            _make_complete_profile(session, user.id)
            # Update with new values
            updated = upsert_profile(
                user_id=user.id,
                target_role="Data Scientist",
                cv_text="ML expert.",
                recipient_email="new@example.com",
                session=session,
            )
            assert updated.target_role == "Data Scientist"
            assert updated.cv_text == "ML expert."
            assert updated.recipient_email == "new@example.com"

    def test_upsert_profile_only_one_row_after_update(self):
        """upsert_profile() does not create duplicate rows on update."""
        with in_memory_session() as session:
            user = _make_user(session)
            _make_complete_profile(session, user.id)
            _make_complete_profile(session, user.id)
            profile = get_profile(user.id, session)
            assert profile is not None  # exactly one row


class TestSetScheduleEnabled:
    def test_set_schedule_enabled_raises_when_no_profile(self):
        """set_schedule_enabled() raises ValueError when no profile exists."""
        with in_memory_session() as session:
            user = _make_user(session)
            with pytest.raises(ValueError):
                set_schedule_enabled(user.id, True, session)

    def test_set_schedule_enabled_raises_when_profile_incomplete(self):
        """set_schedule_enabled() raises ValueError when profile is incomplete."""
        with in_memory_session() as session:
            user = _make_user(session)
            # Profile with empty cv_text
            upsert_profile(
                user_id=user.id,
                target_role="Engineer",
                cv_text="",
                recipient_email="r@example.com",
                session=session,
            )
            with pytest.raises(ValueError):
                set_schedule_enabled(user.id, True, session)

    def test_set_schedule_enabled_succeeds_when_complete(self):
        """set_schedule_enabled() sets the flag when profile is complete."""
        with in_memory_session() as session:
            user = _make_user(session)
            _make_complete_profile(session, user.id)
            set_schedule_enabled(user.id, True, session)
            profile = get_profile(user.id, session)
            assert profile is not None
            assert profile.schedule_enabled is True

    def test_set_schedule_enabled_false_always_succeeds(self):
        """set_schedule_enabled(False) works even on an incomplete profile."""
        with in_memory_session() as session:
            user = _make_user(session)
            upsert_profile(
                user_id=user.id,
                target_role="",
                cv_text="",
                recipient_email="",
                session=session,
            )
            # Disabling should never raise
            set_schedule_enabled(user.id, False, session)
            profile = get_profile(user.id, session)
            assert profile is not None
            assert profile.schedule_enabled is False


class TestGetUserResults:
    def test_get_user_results_returns_empty_list_when_none(self):
        """get_user_results() returns [] when no results exist for the user."""
        with in_memory_session() as session:
            user = _make_user(session)
            results = get_user_results(user.id, session)
            assert results == []

    def test_get_user_results_returns_saved_result(self):
        """get_user_results() returns the saved JobResult."""
        with in_memory_session() as session:
            user = _make_user(session)
            df = _minimal_df()
            save_job_result(user.id, date.today(), df, session)
            results = get_user_results(user.id, session)
            assert len(results) == 1
            assert results[0].user_id == user.id


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def valid_email_strategy():
    """Generate strings of the form localpart@domain.tld."""
    return st.builds(
        lambda local, domain, tld: f"{local}@{domain}.{tld}",
        local=st.text(
            alphabet=st.characters(
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="._+-",
            ),
            min_size=1,
            max_size=20,
        ).filter(lambda s: s[0] not in ".+-" and s[-1] not in ".+-"),
        domain=st.text(
            alphabet=st.characters(
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="-",
            ),
            min_size=1,
            max_size=20,
        ).filter(lambda s: s[0] != "-" and s[-1] != "-"),
        tld=st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Lu")),
            min_size=2,
            max_size=6,
        ),
    )


@st.composite
def incomplete_profile_strategy(draw):
    """Generate a dict with at least one of the three required fields empty/None.

    Returns a dict with keys: target_role, cv_text, recipient_email.
    At least one value is empty string or None.
    """
    # Decide which fields are empty (at least one must be)
    empty_flags = draw(
        st.lists(st.booleans(), min_size=3, max_size=3).filter(any)
    )

    def _field(is_empty: bool):
        if is_empty:
            return draw(st.one_of(st.just(""), st.none()))
        return draw(st.text(min_size=1, max_size=50))

    return {
        "target_role": _field(empty_flags[0]),
        "cv_text": _field(empty_flags[1]),
        "recipient_email": _field(empty_flags[2]),
    }


@st.composite
def user_with_profile_strategy(draw):
    """Generate a dict describing a user + profile for Property 6.

    Keys: email (unique-ish), schedule_enabled, target_role, cv_text, recipient_email.
    """
    # Use a random suffix to reduce email collisions across examples
    suffix = draw(st.integers(min_value=0, max_value=999_999))
    email = f"user{suffix}@test.com"
    schedule_enabled = draw(st.booleans())

    # Randomly decide completeness
    complete = draw(st.booleans())
    if complete:
        target_role = draw(st.text(min_size=1, max_size=30))
        cv_text = draw(st.text(min_size=1, max_size=100))
        recipient_email = draw(st.text(min_size=1, max_size=30))
    else:
        # At least one field is empty
        target_role = draw(st.one_of(st.just(""), st.text(min_size=1, max_size=30)))
        cv_text = draw(st.one_of(st.just(""), st.text(min_size=1, max_size=100)))
        recipient_email = draw(st.one_of(st.just(""), st.text(min_size=1, max_size=30)))
        # Force at least one empty
        if target_role and cv_text and recipient_email:
            target_role = ""

    return {
        "email": email,
        "schedule_enabled": schedule_enabled,
        "target_role": target_role,
        "cv_text": cv_text,
        "recipient_email": recipient_email,
    }


# Alphabet for text columns: letters only, so JSON round-trip never
# misinterprets the value as a number.
_TEXT_ALPHABET = st.characters(whitelist_categories=("Ll", "Lu"))


@st.composite
def valid_dataframe_strategy(draw):
    """Generate a non-empty DataFrame with string and numeric columns.

    String columns use a letters-only alphabet so that JSON serialisation
    never coerces them to numeric types on deserialisation.
    """
    n_rows = draw(st.integers(min_value=1, max_value=10))
    titles = draw(
        st.lists(st.text(_TEXT_ALPHABET, min_size=1, max_size=30), min_size=n_rows, max_size=n_rows)
    )
    companies = draw(
        st.lists(st.text(_TEXT_ALPHABET, min_size=1, max_size=30), min_size=n_rows, max_size=n_rows)
    )
    locations = draw(
        st.lists(st.text(_TEXT_ALPHABET, min_size=1, max_size=30), min_size=n_rows, max_size=n_rows)
    )
    scores = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
            min_size=n_rows,
            max_size=n_rows,
        )
    )
    return pd.DataFrame(
        {
            "title": titles,
            "company": companies,
            "location": locations,
            "match_score": scores,
        }
    )


# ---------------------------------------------------------------------------
# Task 2.7 — Property 2: User creation stores correct data
# ---------------------------------------------------------------------------


@given(valid_email_strategy(), st.text(min_size=8).filter(lambda p: len(p.encode()) <= 72))
@settings(max_examples=100, deadline=None)
def test_user_creation_round_trip(email: str, password: str):
    """Property 2: User creation stores correct data.

    For any valid email and password ≥ 8 chars, create_user must produce a row
    where fetched.email == email and fetched.hashed_password != password.

    **Validates: Requirements 1.2**
    """
    with in_memory_session() as session:
        hashed = _hash(password)
        create_user(email, hashed, session)
        fetched = get_user_by_email(email, session)
        assert fetched is not None, "User not found after creation"
        assert fetched.email == email
        assert fetched.hashed_password != password


# ---------------------------------------------------------------------------
# Task 2.8 — Property 5: Profile completeness gate for schedule
# ---------------------------------------------------------------------------


@given(incomplete_profile_strategy())
@settings(max_examples=100, deadline=None)
def test_schedule_requires_complete_profile(profile_data: dict):
    """Property 5: Profile completeness gate for schedule.

    For any profile with at least one empty required field,
    set_schedule_enabled(user_id, True) must raise ValueError and must not
    set schedule_enabled=True.

    **Validates: Requirements 4.4**
    """
    with in_memory_session() as session:
        user = _make_user(session)
        upsert_profile(
            user_id=user.id,
            target_role=profile_data["target_role"] or "",
            cv_text=profile_data["cv_text"] or "",
            recipient_email=profile_data["recipient_email"] or "",
            session=session,
        )
        with pytest.raises(ValueError):
            set_schedule_enabled(user.id, True, session)
        # Verify the flag was NOT set
        profile = get_profile(user.id, session)
        assert profile is not None
        assert profile.schedule_enabled is False


# ---------------------------------------------------------------------------
# Task 2.9 — Property 6: Scheduled users query correctness
# ---------------------------------------------------------------------------


@given(st.lists(user_with_profile_strategy(), min_size=1, max_size=10))
@settings(max_examples=50, deadline=None)
def test_get_scheduled_users_correctness(users: list[dict]):
    """Property 6: Scheduled users query correctness.

    get_scheduled_users() must return exactly those users where
    schedule_enabled=True AND all three profile fields are non-empty.

    **Validates: Requirements 5.3**
    """
    # Deduplicate emails within this example to avoid IntegrityError
    seen_emails: set[str] = set()
    unique_users = []
    for u in users:
        if u["email"] not in seen_emails:
            seen_emails.add(u["email"])
            unique_users.append(u)

    with in_memory_session() as session:
        created: list[tuple[dict, User]] = []
        for u in unique_users:
            user = create_user(u["email"], _hash("password123"), session)
            upsert_profile(
                user_id=user.id,
                target_role=u["target_role"] or "",
                cv_text=u["cv_text"] or "",
                recipient_email=u["recipient_email"] or "",
                session=session,
            )
            # Only enable schedule if profile is complete and flag is True
            if u["schedule_enabled"] and u["target_role"] and u["cv_text"] and u["recipient_email"]:
                set_schedule_enabled(user.id, True, session)
            created.append((u, user))

        scheduled = get_scheduled_users(session)
        scheduled_ids = {p.user_id for p in scheduled}

        # Compute expected set
        expected_ids = {
            user.id
            for u, user in created
            if u["schedule_enabled"]
            and u["target_role"]
            and u["cv_text"]
            and u["recipient_email"]
        }

        assert scheduled_ids == expected_ids, (
            f"Expected scheduled user IDs {expected_ids}, got {scheduled_ids}"
        )


# ---------------------------------------------------------------------------
# Task 2.10 — Property 7: Job results storage round-trip
# ---------------------------------------------------------------------------


@given(valid_dataframe_strategy())
@settings(max_examples=50, deadline=None)
def test_job_results_round_trip(df: pd.DataFrame):
    """Property 7: Job results storage round-trip.

    save_job_result followed by get_user_results must return a result whose
    to_dataframe() is equivalent to the original DataFrame.

    **Validates: Requirements 5.5, 6.3**
    """
    with in_memory_session() as session:
        user = _make_user(session)
        run_date = date(2024, 1, 15)
        save_job_result(user.id, run_date, df, session)
        results = get_user_results(user.id, session)
        assert len(results) == 1
        recovered = results[0].to_dataframe()
        pd.testing.assert_frame_equal(
            recovered.reset_index(drop=True),
            df.reset_index(drop=True),
            check_like=True,   # column order may differ
            check_dtype=False,  # JSON round-trip may coerce e.g. float 0.0 → int 0
            rtol=1e-5,          # allow minor float precision loss through JSON
        )


# ---------------------------------------------------------------------------
# Task 2.11 — Property 9: User data isolation
# ---------------------------------------------------------------------------


@given(
    st.integers(min_value=1, max_value=5),
    st.integers(min_value=1, max_value=5),
)
@settings(max_examples=50, deadline=None)
def test_user_data_isolation(n_a: int, n_b: int):
    """Property 9: User data isolation.

    Every result from get_user_results(user_a.id) must have user_id == user_a.id.
    No result belonging to user B must appear.

    **Validates: Requirements 7.4**
    """
    with in_memory_session() as session:
        user_a = create_user("a@isolation.com", _hash("password123"), session)
        user_b = create_user("b@isolation.com", _hash("password123"), session)

        df = _minimal_df()
        today = date(2024, 6, 1)

        for _ in range(n_a):
            save_job_result(user_a.id, today, df, session)
        for _ in range(n_b):
            save_job_result(user_b.id, today, df, session)

        results_a = get_user_results(user_a.id, session)
        assert len(results_a) == n_a
        assert all(r.user_id == user_a.id for r in results_a), (
            "Found a result belonging to another user in user_a's results"
        )


# ---------------------------------------------------------------------------
# Task 2.12 — Property 10: Results ordering
# ---------------------------------------------------------------------------


@given(
    st.lists(
        st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31)),
        min_size=2,
        max_size=10,
        unique=True,
    )
)
@settings(max_examples=50, deadline=None)
def test_results_ordered_descending(run_dates: list[date]):
    """Property 10: Results ordering.

    get_user_results(user_id) must return results in descending run_date order
    (most recent first).

    **Validates: Requirements 7.1**
    """
    with in_memory_session() as session:
        user = _make_user(session)
        df = _minimal_df()

        # Insert in arbitrary (unsorted) order
        for d in run_dates:
            save_job_result(user.id, d, df, session)

        results = get_user_results(user.id, session)
        returned_dates = [r.run_date for r in results]

        assert returned_dates == sorted(returned_dates, reverse=True), (
            f"Results not in descending order: {returned_dates}"
        )
