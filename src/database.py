"""
database.py — SQLAlchemy ORM models, engine/session management, and CRUD operations.

Covers tasks 2.1–2.5 of the multi-user-scheduled-delivery spec.
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Generator

import pandas as pd
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.exc import IntegrityError  # re-exported for callers
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

# ---------------------------------------------------------------------------
# Default database path
# ---------------------------------------------------------------------------

DB_PATH: Path = Path.home() / ".job_finder" / "users.db"

# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM Models  (Task 2.1)
# ---------------------------------------------------------------------------


class User(Base):
    """Registered user account."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    profile: Mapped["UserProfile"] = relationship(
        "UserProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    results: Mapped[list["JobResult"]] = relationship(
        "JobResult",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserProfile(Base):
    """Per-user settings: target role, CV text, recipient email, schedule flag."""

    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), primary_key=True
    )
    target_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    cv_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    uk_locations: Mapped[str | None] = mapped_column(Text, nullable=True)             # comma-separated UK cities
    international_locations: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated non-UK cities
    schedule_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    user: Mapped["User"] = relationship("User", back_populates="profile")

    @property
    def is_complete(self) -> bool:
        """True iff target_role, cv_text, and recipient_email are all non-empty."""
        return bool(self.target_role and self.cv_text and self.recipient_email)

    @property
    def uk_location_list(self) -> list[str]:
        """Return UK locations as a list, defaulting to ['London'] if not set."""
        if self.uk_locations and self.uk_locations.strip():
            return [loc.strip() for loc in self.uk_locations.split(",") if loc.strip()]
        return ["London"]

    @property
    def international_location_list(self) -> list[str]:
        """Return international locations as a list (empty if not set)."""
        if self.international_locations and self.international_locations.strip():
            return [loc.strip() for loc in self.international_locations.split(",") if loc.strip()]
        return []


class JobResult(Base):
    """Serialised scored job results for one user for one pipeline run."""

    __tablename__ = "job_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    results_json: Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="results")

    def to_dataframe(self) -> pd.DataFrame:
        """Deserialise results_json back to a DataFrame."""
        return pd.read_json(io.StringIO(self.results_json), orient="records")


# ---------------------------------------------------------------------------
# Engine / session management  (Task 2.2)
# ---------------------------------------------------------------------------

# Module-level engine and session factory; recreated by init_db().
_engine = None
_SessionFactory = None


def init_db(db_path: Path | str = DB_PATH) -> None:
    """Create all tables if they do not already exist.

    Safe to call on every startup — SQLAlchemy's ``create_all`` is idempotent.
    Creates the parent directory if it does not exist.
    """
    global _engine, _SessionFactory

    path = Path(db_path)

    # ":memory:" is a special SQLite URI — no directory to create.
    if str(db_path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path}"
    else:
        url = "sqlite:///:memory:"

    _engine = create_engine(url, connect_args={"check_same_thread": False})
    _SessionFactory = sessionmaker(bind=_engine)
    Base.metadata.create_all(_engine)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy Session; commit on success, rollback on exception, always close."""
    if _SessionFactory is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")

    session: Session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# User CRUD  (Task 2.3)
# ---------------------------------------------------------------------------


def create_user(email: str, hashed_password: str, session: Session) -> User:
    """Insert a new user row.

    Raises:
        sqlalchemy.exc.IntegrityError: if the email already exists.
    """
    user = User(email=email, hashed_password=hashed_password)
    session.add(user)
    session.flush()  # propagate to DB so the id is populated; caller commits
    return user


def get_user_by_email(email: str, session: Session) -> User | None:
    """Return the User with the given email, or None."""
    return session.execute(select(User).where(User.email == email)).scalar_one_or_none()


def get_user_by_id(user_id: int, session: Session) -> User | None:
    """Return the User with the given id, or None."""
    return session.get(User, user_id)


# ---------------------------------------------------------------------------
# Profile CRUD  (Task 2.4)
# ---------------------------------------------------------------------------


def upsert_profile(
    user_id: int,
    target_role: str,
    cv_text: str,
    recipient_email: str,
    session: Session,
    uk_locations: str = "London",
    international_locations: str = "",
) -> UserProfile:
    """Insert or update the user_profiles row for user_id."""
    profile = session.get(UserProfile, user_id)
    if profile is None:
        profile = UserProfile(
            user_id=user_id,
            target_role=target_role,
            cv_text=cv_text,
            recipient_email=recipient_email,
            uk_locations=uk_locations,
            international_locations=international_locations or None,
        )
        session.add(profile)
    else:
        profile.target_role = target_role
        profile.cv_text = cv_text
        profile.recipient_email = recipient_email
        profile.uk_locations = uk_locations
        profile.international_locations = international_locations or None
    session.flush()
    return profile


def get_profile(user_id: int, session: Session) -> UserProfile | None:
    """Return the UserProfile for user_id, or None."""
    return session.get(UserProfile, user_id)


def set_schedule_enabled(user_id: int, enabled: bool, session: Session) -> None:
    """Set schedule_enabled for the given user.

    Raises:
        ValueError: if ``enabled=True`` and the profile is incomplete (missing
            target_role, cv_text, or recipient_email).
    """
    profile = session.get(UserProfile, user_id)
    if profile is None:
        raise ValueError(f"No profile found for user_id={user_id}.")
    if enabled and not profile.is_complete:
        raise ValueError(
            "Cannot enable schedule: target role, CV, and recipient email must all be set."
        )
    profile.schedule_enabled = enabled
    session.flush()


# ---------------------------------------------------------------------------
# Scheduler query and Job Results CRUD  (Task 2.5)
# ---------------------------------------------------------------------------


def get_scheduled_users(session: Session) -> list[UserProfile]:
    """Return all UserProfile rows where schedule_enabled=True and all required
    fields (target_role, cv_text, recipient_email) are non-empty."""
    stmt = select(UserProfile).where(
        UserProfile.schedule_enabled == True,  # noqa: E712
        UserProfile.target_role != None,  # noqa: E711
        UserProfile.target_role != "",
        UserProfile.cv_text != None,  # noqa: E711
        UserProfile.cv_text != "",
        UserProfile.recipient_email != None,  # noqa: E711
        UserProfile.recipient_email != "",
    )
    return list(session.execute(stmt).scalars().all())


def save_job_result(
    user_id: int, run_date: date, df: pd.DataFrame, session: Session
) -> JobResult:
    """Serialise df to JSON and insert a job_results row."""
    results_json = df.to_json(orient="records")
    result = JobResult(user_id=user_id, run_date=run_date, results_json=results_json)
    session.add(result)
    session.flush()
    return result


def get_user_results(user_id: int, session: Session) -> list[JobResult]:
    """Return all JobResult rows for user_id ordered by run_date descending."""
    stmt = (
        select(JobResult)
        .where(JobResult.user_id == user_id)
        .order_by(JobResult.run_date.desc())
    )
    return list(session.execute(stmt).scalars().all())


def get_result_by_id(
    result_id: int, user_id: int, session: Session
) -> JobResult | None:
    """Return a specific JobResult only if it belongs to user_id."""
    stmt = select(JobResult).where(
        JobResult.id == result_id,
        JobResult.user_id == user_id,
    )
    return session.execute(stmt).scalar_one_or_none()
