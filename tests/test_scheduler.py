"""Unit tests for scheduler logic — Requirements 2.2, 3.2, 4.4, 4.5."""

import smtplib

import pandas as pd
import pytest

import src.scheduler as sched


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_config(*, completed_iterations=0, iteration_count=1):
    """Return a minimal valid config with next_run_timestamp in the past."""
    return {
        "target_role": "Software Engineer",
        "cv_path": "/tmp/cv.pdf",
        "run_interval_days": 1,
        "iteration_count": iteration_count,
        "completed_iterations": completed_iterations,
        "next_run_timestamp": "2020-01-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# Test 1 — main() skips setup when a valid config exists (Requirement 2.2)
# ---------------------------------------------------------------------------

def test_main_skips_setup_when_config_exists(monkeypatch):
    """load_config returns a valid config → run_setup must NOT be called."""
    config = _base_config(completed_iterations=0, iteration_count=1)

    calls = {"run_setup": 0}

    def fake_load_config():
        return config

    def fake_run_setup():
        calls["run_setup"] += 1
        return config

    def fake_save_config(cfg):
        pass

    def fake_run_pipeline(cfg):
        return pd.DataFrame()

    def fake_merge_results(df):
        return 0

    monkeypatch.setattr(sched, "load_config", fake_load_config)
    monkeypatch.setattr(sched, "run_setup", fake_run_setup)
    monkeypatch.setattr(sched, "save_config", fake_save_config)
    monkeypatch.setattr(sched, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(sched, "merge_results", fake_merge_results)

    # After one iteration completed_iterations becomes 1 == iteration_count → exit 0
    with pytest.raises(SystemExit) as exc_info:
        sched.main()

    assert exc_info.value.code == 0
    assert calls["run_setup"] == 0, "run_setup should NOT have been called"


# ---------------------------------------------------------------------------
# Test 2 — main() exits cleanly when completed_iterations == iteration_count
#           (Requirement 3.2)
# ---------------------------------------------------------------------------

def test_main_exits_cleanly_when_all_iterations_done(monkeypatch):
    """completed_iterations == iteration_count → SystemExit(0) immediately."""
    config = _base_config(completed_iterations=3, iteration_count=3)

    monkeypatch.setattr(sched, "load_config", lambda: config)
    monkeypatch.setattr(sched, "save_config", lambda cfg: None)

    # run_pipeline should never be reached
    def should_not_be_called(*args, **kwargs):
        raise AssertionError("run_pipeline should not be called when all iterations are done")

    monkeypatch.setattr(sched, "run_pipeline", should_not_be_called)

    with pytest.raises(SystemExit) as exc_info:
        sched.main()

    assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Test 3 — run_pipeline exits with non-zero status when GEMINI_API_KEY missing
#           (Requirement 4.4)
# ---------------------------------------------------------------------------

def test_run_pipeline_exits_when_api_key_missing(monkeypatch):
    """Missing GEMINI_API_KEY → sys.exit(1) before any scraping."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    config = _base_config()

    with pytest.raises(SystemExit) as exc_info:
        sched.run_pipeline(config)

    assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Test 4 — zero-result scrape logs warning and still increments
#           completed_iterations (Requirement 4.5)
# ---------------------------------------------------------------------------

def test_zero_result_scrape_increments_completed_iterations(monkeypatch):
    """jobspy returns empty DataFrame → warning logged, completed_iterations += 1."""
    config = _base_config(completed_iterations=0, iteration_count=1)

    saved_configs = []

    def fake_load_config():
        return config

    def fake_save_config(cfg):
        saved_configs.append(dict(cfg))

    # Patch run_pipeline to simulate zero-result scrape path
    def fake_run_pipeline(cfg):
        return pd.DataFrame()

    def fake_merge_results(df):
        return 0

    monkeypatch.setattr(sched, "load_config", fake_load_config)
    monkeypatch.setattr(sched, "save_config", fake_save_config)
    monkeypatch.setattr(sched, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(sched, "merge_results", fake_merge_results)

    with pytest.raises(SystemExit) as exc_info:
        sched.main()

    # Should exit cleanly after the single iteration completes
    assert exc_info.value.code == 0

    # save_config must have been called with completed_iterations == 1
    assert len(saved_configs) >= 1
    final_config = saved_configs[-1]
    assert final_config["completed_iterations"] == 1


# ---------------------------------------------------------------------------
# Tests for Step 6b — email delivery integration (Requirements 3.5, 3.6, 3.7, 4.2)
# ---------------------------------------------------------------------------


def _base_config_with_email(*, completed_iterations=0, iteration_count=1, recipient_email="user@example.com"):
    """Return a minimal valid config that includes recipient_email."""
    cfg = _base_config(completed_iterations=completed_iterations, iteration_count=iteration_count)
    cfg["recipient_email"] = recipient_email
    return cfg


def _make_nonempty_df():
    """Return a minimal non-empty DataFrame that looks like scored results."""
    return pd.DataFrame({"title": ["Engineer"], "company": ["Acme"], "match_score": [0.9]})


# ---------------------------------------------------------------------------
# Test 5 — send_results_email NOT called when recipient_email is absent
#           (Requirement 4.2)
# ---------------------------------------------------------------------------

def test_email_not_sent_when_recipient_absent(monkeypatch):
    """Config without recipient_email → send_results_email must NOT be called."""
    config = _base_config()  # no recipient_email key
    email_calls = {"count": 0}

    def fake_send(*args, **kwargs):
        email_calls["count"] += 1

    monkeypatch.setattr(sched, "load_config", lambda: config)
    monkeypatch.setattr(sched, "save_config", lambda cfg: None)
    monkeypatch.setattr(sched, "run_pipeline", lambda cfg: _make_nonempty_df())
    monkeypatch.setattr(sched, "merge_results", lambda df: 1)
    monkeypatch.setattr(sched, "send_results_email", fake_send)
    monkeypatch.setattr(sched.pd, "read_csv", lambda path: _make_nonempty_df())

    with pytest.raises(SystemExit) as exc_info:
        sched.main()

    assert exc_info.value.code == 0
    assert email_calls["count"] == 0, "send_results_email should NOT be called when recipient_email is absent"


# ---------------------------------------------------------------------------
# Test 6 — send_results_email NOT called when result_df is empty
#           (Requirement 3.5)
# ---------------------------------------------------------------------------

def test_email_not_sent_when_result_df_empty(monkeypatch):
    """Empty result_df → send_results_email must NOT be called."""
    config = _base_config_with_email()
    email_calls = {"count": 0}

    def fake_send(*args, **kwargs):
        email_calls["count"] += 1

    monkeypatch.setattr(sched, "load_config", lambda: config)
    monkeypatch.setattr(sched, "save_config", lambda cfg: None)
    monkeypatch.setattr(sched, "run_pipeline", lambda cfg: pd.DataFrame())  # empty
    monkeypatch.setattr(sched, "merge_results", lambda df: 0)
    monkeypatch.setattr(sched, "send_results_email", fake_send)
    monkeypatch.setattr(sched.pd, "read_csv", lambda path: pd.DataFrame())

    with pytest.raises(SystemExit) as exc_info:
        sched.main()

    assert exc_info.value.code == 0
    assert email_calls["count"] == 0, "send_results_email should NOT be called when result_df is empty"


# ---------------------------------------------------------------------------
# Test 7 — scheduler continues (no sys.exit) when send_results_email raises
#           SMTPException (Requirement 3.7)
# ---------------------------------------------------------------------------

def test_scheduler_continues_on_smtp_exception(monkeypatch):
    """SMTPException from send_results_email → scheduler must NOT call sys.exit."""
    config = _base_config_with_email()

    def fake_send(*args, **kwargs):
        raise smtplib.SMTPException("connection refused")

    monkeypatch.setattr(sched, "load_config", lambda: config)
    monkeypatch.setattr(sched, "save_config", lambda cfg: None)
    monkeypatch.setattr(sched, "run_pipeline", lambda cfg: _make_nonempty_df())
    monkeypatch.setattr(sched, "merge_results", lambda df: 1)
    monkeypatch.setattr(sched, "send_results_email", fake_send)
    monkeypatch.setattr(sched.pd, "read_csv", lambda path: _make_nonempty_df())

    # Should exit 0 (all iterations done) rather than propagating the SMTP error
    with pytest.raises(SystemExit) as exc_info:
        sched.main()

    assert exc_info.value.code == 0, (
        "Scheduler should exit cleanly (code 0) after SMTP exception, not crash"
    )


# ---------------------------------------------------------------------------
# Test 8 — stdout confirmation line contains the recipient address on success
#           (Requirement 3.6)
# ---------------------------------------------------------------------------

def test_stdout_confirmation_contains_recipient(monkeypatch, capsys):
    """Successful email delivery → stdout must contain the recipient address."""
    recipient = "results@example.com"
    config = _base_config_with_email(recipient_email=recipient)

    monkeypatch.setattr(sched, "load_config", lambda: config)
    monkeypatch.setattr(sched, "save_config", lambda cfg: None)
    monkeypatch.setattr(sched, "run_pipeline", lambda cfg: _make_nonempty_df())
    monkeypatch.setattr(sched, "merge_results", lambda df: 1)
    monkeypatch.setattr(sched, "send_results_email", lambda *args, **kwargs: None)  # success
    monkeypatch.setattr(sched.pd, "read_csv", lambda path: _make_nonempty_df())

    with pytest.raises(SystemExit):
        sched.main()

    captured = capsys.readouterr()
    assert recipient in captured.out, (
        f"Expected '{recipient}' in stdout confirmation, got: {captured.out!r}"
    )


# ---------------------------------------------------------------------------
# Property-based tests for scheduler email integration
# ---------------------------------------------------------------------------

from hypothesis import given, settings, HealthCheck
import hypothesis.strategies as st


# ---------------------------------------------------------------------------
# Shared strategy: valid recipient email addresses (local@domain.tld)
# ---------------------------------------------------------------------------

_scheduler_recipient_strategy = st.builds(
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


# ---------------------------------------------------------------------------
# Property 6: Scheduler stdout confirmation contains the recipient address
# Validates: Requirements 3.6
# ---------------------------------------------------------------------------

@given(recipient=_scheduler_recipient_strategy)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_scheduler_stdout_confirmation_property(recipient: str, monkeypatch, capsys):
    """Property 6: Scheduler stdout confirmation contains the recipient address.

    For any valid recipient address string, when send_results_email() succeeds,
    the scheduler SHALL print a line to stdout that contains that recipient address.

    **Validates: Requirements 3.6**
    """
    config = _base_config_with_email(recipient_email=recipient)

    monkeypatch.setattr(sched, "load_config", lambda: config)
    monkeypatch.setattr(sched, "save_config", lambda cfg: None)
    monkeypatch.setattr(sched, "run_pipeline", lambda cfg: _make_nonempty_df())
    monkeypatch.setattr(sched, "merge_results", lambda df: 1)
    monkeypatch.setattr(sched, "send_results_email", lambda *args, **kwargs: None)  # success
    monkeypatch.setattr(sched.pd, "read_csv", lambda path: _make_nonempty_df())

    with pytest.raises(SystemExit):
        sched.main()

    captured = capsys.readouterr()
    assert recipient in captured.out, (
        f"Expected recipient address {recipient!r} in stdout, got: {captured.out!r}"
    )


# ---------------------------------------------------------------------------
# Property 7: SMTP exception causes warning to stderr without scheduler exit
# Validates: Requirements 3.7
# ---------------------------------------------------------------------------

# Strategy: generate exception instances from a variety of Exception subclasses.
_exception_strategy = st.one_of(
    st.builds(Exception, st.text(max_size=50)),
    st.builds(ValueError, st.text(max_size=50)),
    st.builds(RuntimeError, st.text(max_size=50)),
    st.builds(OSError, st.text(max_size=50)),
    st.builds(ConnectionError, st.text(max_size=50)),
    st.builds(TimeoutError, st.text(max_size=50)),
    st.builds(smtplib.SMTPException, st.text(max_size=50)),
    st.builds(smtplib.SMTPConnectError, st.integers(min_value=400, max_value=599), st.text(max_size=50)),
    st.builds(smtplib.SMTPAuthenticationError, st.integers(min_value=400, max_value=599), st.text(max_size=50)),
)


@given(exc=_exception_strategy)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_smtp_exception_no_exit_property(exc: Exception, monkeypatch, capsys):
    """Property 7: SMTP exception causes warning to stderr without scheduler exit.

    For any exception raised by send_results_email(), the scheduler SHALL catch
    it, print a human-readable warning to stderr, and continue to the next
    iteration without calling sys.exit() with a non-zero code.

    **Validates: Requirements 3.7**
    """
    config = _base_config_with_email()

    def fake_send(*args, **kwargs):
        raise exc

    monkeypatch.setattr(sched, "load_config", lambda: config)
    monkeypatch.setattr(sched, "save_config", lambda cfg: None)
    monkeypatch.setattr(sched, "run_pipeline", lambda cfg: _make_nonempty_df())
    monkeypatch.setattr(sched, "merge_results", lambda df: 1)
    monkeypatch.setattr(sched, "send_results_email", fake_send)
    monkeypatch.setattr(sched.pd, "read_csv", lambda path: _make_nonempty_df())

    # Scheduler should exit cleanly (code 0) after completing all iterations,
    # not crash or exit with a non-zero code due to the SMTP exception.
    with pytest.raises(SystemExit) as exc_info:
        sched.main()

    assert exc_info.value.code == 0, (
        f"Expected clean exit (code 0) after SMTP exception {exc!r}, "
        f"got exit code {exc_info.value.code}"
    )

    captured = capsys.readouterr()
    assert "WARNING" in captured.err, (
        f"Expected a WARNING in stderr after SMTP exception {exc!r}, "
        f"got: {captured.err!r}"
    )
