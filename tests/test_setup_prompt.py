"""Unit tests for src/setup_prompt.py."""

import os
import pytest
from unittest.mock import patch, MagicMock


def test_run_setup_returns_all_keys(tmp_path):
    """Happy path: all inputs valid, returned dict has all 7 required keys."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF")

    inputs = iter([
        "Software Engineer",  # target_role
        str(cv),              # cv_path
        "3",                  # run_interval_days
        "5",                  # iteration_count
        "user@example.com",   # recipient_email
    ])

    with patch("builtins.input", side_effect=inputs):
        from src.setup_prompt import run_setup
        result = run_setup()

    assert set(result.keys()) == {
        "target_role", "cv_path", "run_interval_days",
        "iteration_count", "completed_iterations", "next_run_timestamp",
        "recipient_email",
    }
    assert result["target_role"] == "Software Engineer"
    assert result["cv_path"] == str(cv)
    assert result["run_interval_days"] == 3
    assert result["iteration_count"] == 5
    assert result["completed_iterations"] == 0
    assert result["next_run_timestamp"].endswith("Z")
    assert result["recipient_email"] == "user@example.com"


def test_cv_path_reprompts_on_missing_file(tmp_path):
    """cv_path validation re-prompts when file does not exist."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF")

    inputs = iter([
        "Dev",
        "/nonexistent/cv.pdf",  # bad: file missing
        str(cv),                # good
        "1",
        "1",
        "dev@example.com",
    ])

    with patch("builtins.input", side_effect=inputs):
        from src.setup_prompt import run_setup
        result = run_setup()

    assert result["cv_path"] == str(cv)


def test_cv_path_reprompts_on_bad_extension(tmp_path):
    """cv_path validation re-prompts when extension is not .pdf or .docx."""
    bad = tmp_path / "cv.txt"
    bad.write_bytes(b"text")
    good = tmp_path / "cv.docx"
    good.write_bytes(b"docx")

    inputs = iter([
        "Dev",
        str(bad),   # bad extension
        str(good),  # good
        "1",
        "1",
        "dev@example.com",
    ])

    with patch("builtins.input", side_effect=inputs):
        from src.setup_prompt import run_setup
        result = run_setup()

    assert result["cv_path"] == str(good)


def test_run_interval_reprompts_on_zero(tmp_path):
    """run_interval_days re-prompts when value is 0."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF")

    inputs = iter(["Dev", str(cv), "0", "2", "1", "dev@example.com"])

    with patch("builtins.input", side_effect=inputs):
        from src.setup_prompt import run_setup
        result = run_setup()

    assert result["run_interval_days"] == 2


def test_run_interval_reprompts_on_non_integer(tmp_path):
    """run_interval_days re-prompts when value is not an integer."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF")

    inputs = iter(["Dev", str(cv), "abc", "3", "1", "dev@example.com"])

    with patch("builtins.input", side_effect=inputs):
        from src.setup_prompt import run_setup
        result = run_setup()

    assert result["run_interval_days"] == 3


def test_iteration_count_reprompts_on_zero(tmp_path):
    """iteration_count re-prompts when value is 0."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF")

    inputs = iter(["Dev", str(cv), "1", "0", "5", "dev@example.com"])

    with patch("builtins.input", side_effect=inputs):
        from src.setup_prompt import run_setup
        result = run_setup()

    assert result["iteration_count"] == 5


def test_keyboard_interrupt_raises_system_exit(tmp_path):
    """KeyboardInterrupt during input raises SystemExit."""
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        from src.setup_prompt import run_setup
        with pytest.raises(SystemExit):
            run_setup()


def test_completed_iterations_initialised_to_zero(tmp_path):
    """completed_iterations is always 0 after setup."""
    cv = tmp_path / "cv.docx"
    cv.write_bytes(b"docx")

    inputs = iter(["Analyst", str(cv), "7", "10", "analyst@example.com"])

    with patch("builtins.input", side_effect=inputs):
        from src.setup_prompt import run_setup
        result = run_setup()

    assert result["completed_iterations"] == 0


def test_next_run_timestamp_is_iso8601_utc(tmp_path):
    """next_run_timestamp is a valid ISO-8601 UTC string ending with Z."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF")

    inputs = iter(["Dev", str(cv), "1", "1", "dev@example.com"])

    with patch("builtins.input", side_effect=inputs):
        from src.setup_prompt import run_setup
        result = run_setup()

    ts = result["next_run_timestamp"]
    assert ts.endswith("Z")
    # Should parse without error
    from datetime import datetime
    datetime.fromisoformat(ts.rstrip("Z"))


# ---------------------------------------------------------------------------
# Tests for _prompt_email() validation behaviour (Requirements 1.2, 1.3)
# ---------------------------------------------------------------------------

def test_prompt_email_valid_on_first_attempt():
    """A valid email address is accepted immediately without re-prompting."""
    from src.setup_prompt import _prompt_email

    with patch("builtins.input", return_value="user@example.com") as mock_input:
        result = _prompt_email()

    assert result == "user@example.com"
    mock_input.assert_called_once()


def test_prompt_email_reprompts_when_no_at_sign():
    """An address with no '@' causes re-prompting; valid address is then accepted."""
    from src.setup_prompt import _prompt_email

    inputs = iter(["notanemail", "user@example.com"])
    with patch("builtins.input", side_effect=inputs):
        result = _prompt_email()

    assert result == "user@example.com"


def test_prompt_email_reprompts_when_two_at_signs():
    """An address with two '@' characters causes re-prompting."""
    from src.setup_prompt import _prompt_email

    inputs = iter(["user@@example.com", "user@example.com"])
    with patch("builtins.input", side_effect=inputs):
        result = _prompt_email()

    assert result == "user@example.com"


def test_prompt_email_reprompts_when_domain_has_no_dot():
    """An address whose domain part contains no '.' causes re-prompting."""
    from src.setup_prompt import _prompt_email

    inputs = iter(["user@nodomain", "user@example.com"])
    with patch("builtins.input", side_effect=inputs):
        result = _prompt_email()

    assert result == "user@example.com"


def test_run_setup_returns_dict_with_recipient_email_key(tmp_path):
    """run_setup() return value contains the 'recipient_email' key."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF")

    inputs = iter(["Dev", str(cv), "1", "1", "dev@example.com"])

    with patch("builtins.input", side_effect=inputs):
        from src.setup_prompt import run_setup
        result = run_setup()

    assert "recipient_email" in result
    assert result["recipient_email"] == "dev@example.com"


def test_prompt_email_reprompts_multiple_invalid_then_accepts():
    """Multiple consecutive invalid addresses all cause re-prompting before a valid one."""
    from src.setup_prompt import _prompt_email

    inputs = iter([
        "noemail",          # no '@'
        "two@@at.com",      # two '@'
        "user@nodomain",    # no '.' in domain
        "valid@test.org",   # valid
    ])
    with patch("builtins.input", side_effect=inputs):
        result = _prompt_email()

    assert result == "valid@test.org"


# ---------------------------------------------------------------------------
# Property-based tests for email validation (Property 1)
# Validates: Requirements 1.2, 1.3
# ---------------------------------------------------------------------------
# **Validates: Requirements 1.2, 1.3**

from hypothesis import given, settings
import hypothesis.strategies as st


def _is_valid_email(s: str) -> bool:
    """Mirror of the validation logic in _prompt_email()."""
    parts = s.split("@")
    if len(parts) != 2:
        return False
    domain = parts[1]
    return "." in domain


@given(st.text())
@settings(max_examples=500)
def test_email_validation_property(s: str):
    """Property 1: Email validation accepts valid addresses and rejects invalid ones.

    For any string s, the validation logic accepts s if and only if:
      - s.count('@') == 1, AND
      - '.' in s.split('@')[1]

    **Validates: Requirements 1.2, 1.3**
    """
    from src.setup_prompt import _prompt_email

    expected_valid = (s.count("@") == 1) and ("." in s.split("@")[1])

    if expected_valid:
        # The function should accept s on the first call and return it
        with patch("builtins.input", return_value=s) as mock_input:
            result = _prompt_email()
        assert result == s
        mock_input.assert_called_once()
    else:
        # The function should reject s and keep prompting; supply a known-good
        # fallback so the loop terminates.
        fallback = "fallback@example.com"
        inputs = iter([s, fallback])
        with patch("builtins.input", side_effect=inputs):
            result = _prompt_email()
        assert result == fallback
