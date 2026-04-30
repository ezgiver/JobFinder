"""Interactive CLI setup prompt for the scheduled autonomous execution mode."""

import os
from datetime import datetime, timezone


def run_setup() -> dict:
    """Interactively collect and validate all config fields.

    Returns a config dict with keys:
        target_role, cv_path, run_interval_days,
        iteration_count, completed_iterations, next_run_timestamp,
        recipient_email
    Raises SystemExit on keyboard interrupt.
    """
    try:
        target_role = input("Enter target role: ").strip()

        cv_path = _prompt_cv_path()
        run_interval_days = _prompt_positive_int(
            "Enter run interval in days (≥ 1): ", "run_interval_days"
        )
        iteration_count = _prompt_positive_int(
            "Enter total number of iterations (≥ 1): ", "iteration_count"
        )
        recipient_email = _prompt_email()
    except KeyboardInterrupt:
        raise SystemExit(1)

    return {
        "target_role": target_role,
        "cv_path": cv_path,
        "run_interval_days": run_interval_days,
        "iteration_count": iteration_count,
        "completed_iterations": 0,
        "next_run_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        "recipient_email": recipient_email,
    }


def _prompt_cv_path() -> str:
    """Prompt for a CV path until a valid one is provided."""
    while True:
        path = input("Enter absolute path to your CV (.pdf or .docx): ").strip()
        _, ext = os.path.splitext(path)
        if not os.path.isfile(path):
            print(f"Error: file not found: {path!r}")
        elif ext.lower() not in (".pdf", ".docx"):
            print(f"Error: unsupported file type {ext!r}. Must be .pdf or .docx.")
        else:
            return path


def _prompt_positive_int(prompt: str, field_name: str) -> int:
    """Prompt for a positive integer (≥ 1) until a valid one is provided."""
    while True:
        raw = input(prompt).strip()
        try:
            value = int(raw)
        except ValueError:
            print(f"Error: {field_name} must be a positive integer.")
            continue
        if value < 1:
            print(f"Error: {field_name} must be ≥ 1.")
            continue
        return value


def _prompt_email() -> str:
    """Prompt for a recipient email address until a valid one is provided.

    Validation: exactly one '@', and the domain part (after '@') contains
    at least one '.'.
    """
    while True:
        address = input("Enter recipient email address: ").strip()
        parts = address.split("@")
        if len(parts) != 2:
            print("Error: email address must contain exactly one '@' character.")
            continue
        domain = parts[1]
        if "." not in domain:
            print("Error: domain part of email address must contain at least one '.'.")
            continue
        return address
