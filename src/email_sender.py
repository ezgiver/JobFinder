"""Email delivery module for job finder results.

Provides `send_results_email()` as the public entry point, backed by
private helpers for reading SMTP settings, building the MIME message,
and managing the SMTP connection.
"""

from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

import pandas as pd


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _read_smtp_settings() -> tuple[str, int, str, str | None]:
    """Read SMTP connection settings from environment variables.

    Required variables: ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USERNAME``.
    Optional variable:  ``SMTP_PASSWORD`` (returns ``None`` when absent).

    Returns
    -------
    tuple[str, int, str, str | None]
        ``(host, port, username, password_or_None)``

    Raises
    ------
    EnvironmentError
        If any of the required variables are missing.  The message lists
        every missing variable name.
    """
    host = os.environ.get("SMTP_HOST")
    port_str = os.environ.get("SMTP_PORT")
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")  # optional

    missing = [
        name
        for name, value in (
            ("SMTP_HOST", host),
            ("SMTP_PORT", port_str),
            ("SMTP_USERNAME", username),
        )
        if value is None
    ]

    if missing:
        raise EnvironmentError(
            f"Missing required SMTP environment variable(s): {', '.join(missing)}"
        )

    return host, int(port_str), username, password  # type: ignore[return-value]


def _build_message(
    df: pd.DataFrame,
    run_date: datetime,
    summary: dict,
    recipient: str,
    smtp_username: str,
) -> MIMEMultipart:
    """Build the MIME message with a plain-text body and CSV attachment.

    Parameters
    ----------
    df:
        Scored results DataFrame for this run.
    run_date:
        UTC datetime of run completion; used for subject and attachment name.
    summary:
        Dict with keys ``jobs_scored``, ``new_jobs_added``, ``total_in_csv``.
    recipient:
        Destination email address.
    smtp_username:
        Value used as the ``From`` header.

    Returns
    -------
    MIMEMultipart
        Fully assembled MIME message ready to send.
    """
    date_str = run_date.strftime("%Y-%m-%d")

    msg = MIMEMultipart()
    msg["From"] = smtp_username
    msg["To"] = recipient
    msg["Subject"] = f"Job Finder Results \u2013 {date_str}"

    body = (
        f"Jobs scored: {summary['jobs_scored']}\n"
        f"New jobs added: {summary['new_jobs_added']}\n"
        f"Total jobs in CSV: {summary['total_in_csv']}"
    )
    msg.attach(MIMEText(body, "plain"))

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    attachment = MIMEBase("text", "csv")
    attachment.set_payload(csv_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header(
        "Content-Disposition",
        "attachment",
        filename=f"results_{date_str}.csv",
    )
    msg.attach(attachment)

    return msg


def _connect_and_send(
    host: str,
    port: int,
    username: str,
    password: str | None,
    message: MIMEMultipart,
) -> None:
    """Open an SMTP connection, authenticate if a password is provided, and send.

    Uses ``smtplib.SMTP_SSL`` when *port* is 465; otherwise uses
    ``smtplib.SMTP`` with STARTTLS.

    Parameters
    ----------
    host:
        SMTP server hostname.
    port:
        SMTP server port.
    username:
        Login username.
    password:
        Login password, or ``None`` to skip authentication.
    message:
        Assembled MIME message to deliver.

    Raises
    ------
    smtplib.SMTPException
        Propagated to the caller on any connection, authentication, or send
        failure.
    """
    if port == 465:
        with smtplib.SMTP_SSL(host, port) as smtp:
            if password is not None:
                smtp.login(username, password)
            smtp.send_message(message)
            smtp.quit()
    else:
        with smtplib.SMTP(host, port) as smtp:
            smtp.starttls()
            if password is not None:
                smtp.login(username, password)
            smtp.send_message(message)
            smtp.quit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_results_email(
    df: pd.DataFrame,
    run_date: datetime,
    summary: dict,
    recipient: str,
) -> None:
    """Compose and send the results email.

    Parameters
    ----------
    df:
        The scored results DataFrame for this run (already filtered to
        non-empty by the caller).
    run_date:
        UTC datetime of the run completion, used for subject and attachment
        name.
    summary:
        Dict with keys:

        - ``jobs_scored``    – int, number of rows in *df*
        - ``new_jobs_added`` – int, net-new rows merged into cumulative CSV
        - ``total_in_csv``   – int, total rows in cumulative CSV after merge

    recipient:
        Destination email address.

    Raises
    ------
    EnvironmentError
        If ``SMTP_HOST``, ``SMTP_PORT``, or ``SMTP_USERNAME`` are not set.
    smtplib.SMTPException
        Propagated to the caller on connection / authentication / send failure.
    """
    host, port, username, password = _read_smtp_settings()
    message = _build_message(df, run_date, summary, recipient, username)
    _connect_and_send(host, port, username, password, message)
