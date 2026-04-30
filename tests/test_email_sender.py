"""Tests for src/email_sender.py.

This file contains property-based tests for:
- Property 4: Missing required SMTP variables produce a descriptive EnvironmentError.
- Property 5: Built message structure is correct for any inputs.

**Validates: Requirements 2.4, 3.1, 3.2, 3.3, 3.4**
"""

from __future__ import annotations

import email.message
import os
import quopri
import base64
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest
from hypothesis import given, settings
import hypothesis.strategies as st

# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

# The three required SMTP variable names.
_REQUIRED_SMTP_VARS = ["SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME"]


@given(
    st.lists(
        st.sampled_from(_REQUIRED_SMTP_VARS),
        min_size=1,
        unique=True,
    )
)
@settings(max_examples=100, deadline=None)
def test_missing_smtp_vars_error(missing_vars: list[str]):
    """Property 4: Missing required SMTP variables produce a descriptive EnvironmentError.

    For any non-empty subset of {SMTP_HOST, SMTP_PORT, SMTP_USERNAME} that is
    absent from the environment, _read_smtp_settings() SHALL raise an
    EnvironmentError whose message names every missing variable in that subset.

    **Validates: Requirements 2.4**
    """
    from src.email_sender import _read_smtp_settings

    # Build a complete environment with all three required vars present, then
    # remove the ones in the generated missing subset.
    base_env = {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "user@example.com",
        "SMTP_PASSWORD": "secret",
    }
    for var in missing_vars:
        base_env.pop(var, None)

    with patch.dict(os.environ, base_env, clear=True):
        with pytest.raises(EnvironmentError) as exc_info:
            _read_smtp_settings()

    error_message = str(exc_info.value)
    for var in missing_vars:
        assert var in error_message, (
            f"Expected missing variable '{var}' to appear in EnvironmentError "
            f"message, but got: {error_message!r}"
        )


# ---------------------------------------------------------------------------
# Strategies for Property 5
# ---------------------------------------------------------------------------

# A strategy that generates a non-empty pandas DataFrame with at least one row.
# We use a fixed set of column names to keep things simple and deterministic.
_df_strategy = st.fixed_dictionaries(
    {
        "title": st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=10),
        "company": st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=10),
        "score": st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=1, max_size=10),
    }
).map(
    lambda d: pd.DataFrame(
        {
            "title": d["title"][: min(len(d["title"]), len(d["company"]), len(d["score"]))],
            "company": d["company"][: min(len(d["title"]), len(d["company"]), len(d["score"]))],
            "score": d["score"][: min(len(d["title"]), len(d["company"]), len(d["score"]))],
        }
    )
).filter(lambda df: len(df) > 0)

# UTC datetime strategy: any date between 2000-01-01 and 2099-12-31.
_run_date_strategy = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2099, 12, 31),
    timezones=st.just(timezone.utc),
)

# Summary dict strategy: integer counts for the three required keys.
_summary_strategy = st.fixed_dictionaries(
    {
        "jobs_scored": st.integers(min_value=0, max_value=10_000),
        "new_jobs_added": st.integers(min_value=0, max_value=10_000),
        "total_in_csv": st.integers(min_value=0, max_value=100_000),
    }
)

# Valid recipient email strategy: local@domain.tld
_recipient_strategy = st.builds(
    lambda local, domain, tld: f"{local}@{domain}.{tld}",
    local=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="._+-"),
        min_size=1,
        max_size=20,
    ).filter(lambda s: s[0] not in ".+-" and s[-1] not in ".+-"),
    domain=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-"),
        min_size=1,
        max_size=20,
    ).filter(lambda s: s[0] != "-" and s[-1] != "-"),
    tld=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu")),
        min_size=2,
        max_size=6,
    ),
)


@given(
    df=_df_strategy,
    run_date=_run_date_strategy,
    summary=_summary_strategy,
    recipient=_recipient_strategy,
)
@settings(max_examples=100, deadline=None)
def test_message_structure(
    df: pd.DataFrame,
    run_date: datetime,
    summary: dict,
    recipient: str,
) -> None:
    """Property 5: Built message structure is correct for any inputs.

    For any non-empty scored DataFrame, UTC run_date, summary dict with integer
    counts, and valid recipient address, _build_message() SHALL produce a MIME
    message where:
    - the To header equals the recipient address,
    - the Subject header equals 'Job Finder Results – <YYYY-MM-DD>' using the
      UTC date of run_date,
    - the plain-text body contains the jobs_scored, new_jobs_added, and
      total_in_csv values from the summary dict,
    - there is exactly one attachment with filename results_<YYYY-MM-DD>.csv
      using the UTC date of run_date.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
    """
    from src.email_sender import _build_message

    smtp_username = "sender@example.com"
    msg = _build_message(df, run_date, summary, recipient, smtp_username)

    date_str = run_date.strftime("%Y-%m-%d")

    # --- To header ---
    assert msg["To"] == recipient, (
        f"Expected To={recipient!r}, got {msg['To']!r}"
    )

    # --- Subject header ---
    expected_subject = f"Job Finder Results \u2013 {date_str}"
    assert msg["Subject"] == expected_subject, (
        f"Expected Subject={expected_subject!r}, got {msg['Subject']!r}"
    )

    # --- Body and attachment parts ---
    parts = list(msg.walk())
    # Filter to non-multipart parts only
    non_multipart = [p for p in parts if not p.get_content_type().startswith("multipart/")]

    plain_parts = [p for p in non_multipart if p.get_content_type() == "text/plain"]
    attachment_parts = [
        p for p in non_multipart if p.get_content_disposition() == "attachment"
    ]

    # --- Plain-text body contains summary values ---
    assert len(plain_parts) >= 1, "Expected at least one text/plain part"
    body_text = plain_parts[0].get_payload(decode=True)
    if body_text is None:
        # get_payload without decode=True for non-encoded parts
        body_text = plain_parts[0].get_payload()
        if isinstance(body_text, str):
            body_text_str = body_text
        else:
            body_text_str = body_text.decode("utf-8")
    else:
        body_text_str = body_text.decode("utf-8")

    assert str(summary["jobs_scored"]) in body_text_str, (
        f"Expected jobs_scored={summary['jobs_scored']} in body, got: {body_text_str!r}"
    )
    assert str(summary["new_jobs_added"]) in body_text_str, (
        f"Expected new_jobs_added={summary['new_jobs_added']} in body, got: {body_text_str!r}"
    )
    assert str(summary["total_in_csv"]) in body_text_str, (
        f"Expected total_in_csv={summary['total_in_csv']} in body, got: {body_text_str!r}"
    )

    # --- Exactly one attachment with correct filename ---
    assert len(attachment_parts) == 1, (
        f"Expected exactly 1 attachment, got {len(attachment_parts)}"
    )
    attachment = attachment_parts[0]
    filename = attachment.get_filename()
    expected_filename = f"results_{date_str}.csv"
    assert filename == expected_filename, (
        f"Expected attachment filename={expected_filename!r}, got {filename!r}"
    )


# ---------------------------------------------------------------------------
# Property 3: STARTTLS is used for any port other than 465
# ---------------------------------------------------------------------------

@given(
    port=st.integers().filter(lambda p: p != 465),
)
@settings(max_examples=100, deadline=None)
def test_starttls_for_non_465_ports(port: int) -> None:
    """Property 3: STARTTLS is used for any port other than 465.

    For any integer port value that is not 465, _connect_and_send() SHALL
    establish the connection using smtplib.SMTP and call starttls() before
    authenticating, and SHALL NOT use smtplib.SMTP_SSL.

    **Validates: Requirements 2.3**
    """
    from src.email_sender import _connect_and_send
    from unittest.mock import MagicMock, patch

    mock_smtp_instance = MagicMock()
    mock_smtp_ssl_instance = MagicMock()

    with patch("smtplib.SMTP") as mock_smtp_cls, \
         patch("smtplib.SMTP_SSL") as mock_smtp_ssl_cls:
        # Make the context manager return our mock instance
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_smtp_ssl_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_ssl_instance)
        mock_smtp_ssl_cls.return_value.__exit__ = MagicMock(return_value=False)

        from email.mime.multipart import MIMEMultipart
        dummy_message = MIMEMultipart()

        _connect_and_send(
            host="smtp.example.com",
            port=port,
            username="user@example.com",
            password="secret",
            message=dummy_message,
        )

    # smtplib.SMTP must have been used
    mock_smtp_cls.assert_called_once_with("smtp.example.com", port)

    # starttls() must have been called on the SMTP instance
    mock_smtp_instance.starttls.assert_called_once()

    # smtplib.SMTP_SSL must NOT have been used
    mock_smtp_ssl_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Unit tests for _connect_and_send() and _read_smtp_settings()
# ---------------------------------------------------------------------------

def test_connect_and_send_uses_smtp_ssl_for_port_465() -> None:
    """Test that SMTP_SSL is used when port is 465.

    **Validates: Requirements 2.2**
    """
    from src.email_sender import _connect_and_send
    from unittest.mock import MagicMock, patch
    from email.mime.multipart import MIMEMultipart

    mock_ssl_instance = MagicMock()

    with patch("smtplib.SMTP_SSL") as mock_smtp_ssl_cls, \
         patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_ssl_cls.return_value.__enter__ = MagicMock(return_value=mock_ssl_instance)
        mock_smtp_ssl_cls.return_value.__exit__ = MagicMock(return_value=False)

        dummy_message = MIMEMultipart()
        _connect_and_send(
            host="smtp.example.com",
            port=465,
            username="user@example.com",
            password="secret",
            message=dummy_message,
        )

    # SMTP_SSL must have been used
    mock_smtp_ssl_cls.assert_called_once_with("smtp.example.com", 465)

    # Plain SMTP must NOT have been used
    mock_smtp_cls.assert_not_called()


def test_connect_and_send_skips_login_when_no_password() -> None:
    """Test that login() is skipped when SMTP_PASSWORD is absent (None).

    **Validates: Requirements 2.5**
    """
    from src.email_sender import _connect_and_send
    from unittest.mock import MagicMock, patch
    from email.mime.multipart import MIMEMultipart

    mock_smtp_instance = MagicMock()

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        dummy_message = MIMEMultipart()
        _connect_and_send(
            host="smtp.example.com",
            port=587,
            username="user@example.com",
            password=None,  # no password
            message=dummy_message,
        )

    # login() must NOT have been called
    mock_smtp_instance.login.assert_not_called()

    # starttls() and send_message() should still be called
    mock_smtp_instance.starttls.assert_called_once()
    mock_smtp_instance.send_message.assert_called_once_with(dummy_message)


def test_read_smtp_settings_returns_correct_tuple() -> None:
    """Test that _read_smtp_settings() returns the correct tuple when all env vars are set.

    **Validates: Requirements 2.1**
    """
    from src.email_sender import _read_smtp_settings

    env = {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "user@example.com",
        "SMTP_PASSWORD": "mypassword",
    }

    with patch.dict(os.environ, env, clear=True):
        host, port, username, password = _read_smtp_settings()

    assert host == "smtp.example.com"
    assert port == 587
    assert isinstance(port, int)
    assert username == "user@example.com"
    assert password == "mypassword"


def test_read_smtp_settings_password_none_when_absent() -> None:
    """Test that _read_smtp_settings() returns None for password when SMTP_PASSWORD is absent.

    **Validates: Requirements 2.5**
    """
    from src.email_sender import _read_smtp_settings

    env = {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "465",
        "SMTP_USERNAME": "user@example.com",
        # SMTP_PASSWORD intentionally absent
    }

    with patch.dict(os.environ, env, clear=True):
        host, port, username, password = _read_smtp_settings()

    assert host == "smtp.example.com"
    assert port == 465
    assert username == "user@example.com"
    assert password is None
