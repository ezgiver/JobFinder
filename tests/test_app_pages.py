"""
tests/test_app_pages.py — Unit tests for src/app.py UI page logic.

Covers task 6.6 of the multi-user-scheduled-delivery spec.

Strategy
--------
Two complementary approaches are used:

1. **AppTest** (``streamlit.testing.v1.AppTest``) — drives the full app in a
   headless Streamlit runtime.  Used for login/register flows where we need
   to interact with widgets and inspect rendered output.

2. **Direct function calls with mocked ``streamlit``** — calls page functions
   directly after patching ``streamlit.*`` calls.  Used for profile, search,
   and history pages where the AppTest widget-interaction model is fragile
   against mocked DB calls.

All external I/O (DB, pipeline, email, scheduler) is patched via
``unittest.mock`` / ``pytest-mock``.

Requirements tested: 1.3, 2.3, 2.4, 3.7, 4.1, 6.1, 7.3
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Ensure src/ is importable
# ---------------------------------------------------------------------------
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import src.database as _db
from src.database import (
    create_user,
    get_profile,
    get_session,
    get_user_results,
    init_db,
    save_job_result,
    set_schedule_enabled,
    upsert_profile,
)

APP_PATH = str(Path(__file__).parent.parent / "src" / "app.py")

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


def _minimal_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "title": ["Engineer"],
            "company": ["Acme"],
            "location": ["Remote"],
            "match_score": [85],
            "job_url": ["https://example.com/job/1"],
        }
    )


def _make_complete_profile_mock(schedule_enabled: bool = False) -> MagicMock:
    """Return a MagicMock that looks like a complete UserProfile."""
    p = MagicMock()
    p.target_role = "Software Engineer"
    p.cv_text = "Experienced developer."
    p.recipient_email = "r@example.com"
    p.schedule_enabled = schedule_enabled
    p.is_complete = True
    return p


def _make_incomplete_profile_mock() -> MagicMock:
    """Return a MagicMock that looks like an incomplete UserProfile."""
    p = MagicMock()
    p.target_role = "Engineer"
    p.cv_text = ""
    p.recipient_email = ""
    p.schedule_enabled = False
    p.is_complete = False
    return p


def _session_ctx(return_value=None):
    """Return a context-manager mock that yields *return_value* as the session."""
    mock_session = MagicMock() if return_value is None else return_value
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# AppTest helpers
# ---------------------------------------------------------------------------


def _apptest_unauthenticated(extra_patches=None):
    """Return an AppTest instance for the unauthenticated (login/register) view."""
    from streamlit.testing.v1 import AppTest

    patches = {
        "src.app.init_db": MagicMock(),
        "src.app.get_scheduler": MagicMock(),
    }
    if extra_patches:
        patches.update(extra_patches)

    with _multi_patch(patches):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.run()
    return at


@contextmanager
def _multi_patch(patches: dict):
    """Context manager that applies multiple patches simultaneously."""
    with patch.multiple("builtins", **{}):  # no-op anchor
        active = []
        try:
            for target, new in patches.items():
                p = patch(target, new)
                p.start()
                active.append(p)
            yield
        finally:
            for p in reversed(active):
                p.stop()


# ---------------------------------------------------------------------------
# render_login_register_page — AppTest tests
# ---------------------------------------------------------------------------


class TestLoginPage:
    """Login tab tests — mix of AppTest and direct-call approaches."""

    def test_correct_credentials_sets_session_state(self, mocker):
        """Correct credentials → user_id and user_email set in session state.

        Uses direct function call to avoid AppTest re-importing app.py on
        st.rerun(), which would rebind patched names.

        Requirement 2.2
        """
        import streamlit as st

        mock_user = MagicMock()
        mock_user.id = 42
        mock_user.email = "good@example.com"
        mock_user.hashed_password = _hash("correctpass")

        # Patch st.tabs to return two context-manager mocks
        login_tab = MagicMock()
        login_tab.__enter__ = MagicMock(return_value=login_tab)
        login_tab.__exit__ = MagicMock(return_value=False)
        register_tab = MagicMock()
        register_tab.__enter__ = MagicMock(return_value=register_tab)
        register_tab.__exit__ = MagicMock(return_value=False)

        mocker.patch("streamlit.title")
        mocker.patch("streamlit.tabs", return_value=[login_tab, register_tab])
        mocker.patch("streamlit.subheader")
        # Return the right value based on the widget key
        def _text_input(label, value="", key=None, **kw):
            if key == "login_email":
                return "good@example.com"
            if key == "login_password":
                return "correctpass"
            return value

        mocker.patch("streamlit.text_input", side_effect=_text_input)
        # button: True for "Log In" (key="login_btn"), False for everything else
        def _button(label, key=None, **kw):
            return key == "login_btn"

        mocker.patch("streamlit.button", side_effect=_button)
        mocker.patch("streamlit.error")
        mock_rerun = mocker.patch("streamlit.rerun")

        mocker.patch("src.app.get_session", return_value=_session_ctx())
        mocker.patch("src.app.get_user_by_email", return_value=mock_user)
        mocker.patch("src.app.verify_password", return_value=True)

        from src.app import render_login_register_page

        render_login_register_page()

        assert st.session_state["user_id"] == 42
        assert st.session_state["user_email"] == "good@example.com"
        mock_rerun.assert_called_once()

    def test_wrong_password_shows_error(self):
        """Wrong password → 'Invalid credentials.' error; user_id stays None.

        Requirement 2.4
        """
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.email = "user@example.com"
        mock_user.hashed_password = _hash("correctpass")

        from streamlit.testing.v1 import AppTest

        with (
            patch("src.app.init_db"),
            patch("src.app.get_scheduler"),
            patch("src.app.get_session", return_value=_session_ctx()),
            patch("src.app.get_user_by_email", return_value=mock_user),
            patch("src.app.verify_password", return_value=False),
        ):
            at = AppTest.from_file(APP_PATH, default_timeout=30)
            at.run()

            at.text_input(key="login_email").set_value("user@example.com")
            at.text_input(key="login_password").set_value("wrongpass")
            at.button(key="login_btn").click()
            at.run()

        assert any("Invalid credentials" in e.value for e in at.error)
        # user_id should not have been set (still None from init)
        assert at.session_state["user_id"] is None

    def test_unknown_email_shows_same_error_as_wrong_password(self):
        """Unknown email → same 'Invalid credentials.' message (no user enumeration).

        Requirement 2.3
        """
        from streamlit.testing.v1 import AppTest

        with (
            patch("src.app.init_db"),
            patch("src.app.get_scheduler"),
            patch("src.app.get_session", return_value=_session_ctx()),
            patch("src.app.get_user_by_email", return_value=None),
        ):
            at = AppTest.from_file(APP_PATH, default_timeout=30)
            at.run()

            at.text_input(key="login_email").set_value("nobody@example.com")
            at.text_input(key="login_password").set_value("anypassword")
            at.button(key="login_btn").click()
            at.run()

        assert any("Invalid credentials" in e.value for e in at.error)


class TestRegisterPage:
    """Register tab tests using AppTest."""

    def test_duplicate_email_shows_error(self):
        """Duplicate email on register → 'Email already registered.' error.

        Requirement 1.3
        """
        from streamlit.testing.v1 import AppTest

        with (
            patch("src.app.init_db"),
            patch("src.app.get_scheduler"),
            patch("src.app.get_session", return_value=_session_ctx()),
            patch("src.app.create_user", side_effect=IntegrityError("", "", "")),
        ):
            at = AppTest.from_file(APP_PATH, default_timeout=30)
            at.run()

            at.text_input(key="reg_email").set_value("dup@example.com")
            at.text_input(key="reg_password").set_value("password123")
            at.button(key="register_btn").click()
            at.run()

        assert any("already registered" in e.value for e in at.error)

    def test_invalid_email_shows_error(self):
        """Malformed email → 'Invalid email address.' error."""
        from streamlit.testing.v1 import AppTest

        with (
            patch("src.app.init_db"),
            patch("src.app.get_scheduler"),
        ):
            at = AppTest.from_file(APP_PATH, default_timeout=30)
            at.run()

            at.text_input(key="reg_email").set_value("notanemail")
            at.text_input(key="reg_password").set_value("password123")
            at.button(key="register_btn").click()
            at.run()

        assert any("Invalid email" in e.value for e in at.error)

    def test_short_password_shows_error(self):
        """Password < 8 chars → password length error."""
        from streamlit.testing.v1 import AppTest

        with (
            patch("src.app.init_db"),
            patch("src.app.get_scheduler"),
        ):
            at = AppTest.from_file(APP_PATH, default_timeout=30)
            at.run()

            at.text_input(key="reg_email").set_value("user@example.com")
            at.text_input(key="reg_password").set_value("short")
            at.button(key="register_btn").click()
            at.run()

        assert any("8 characters" in e.value for e in at.error)


# ---------------------------------------------------------------------------
# render_profile_page — direct call tests
# ---------------------------------------------------------------------------


class TestProfilePageDirect:
    """Direct-call tests for render_profile_page.

    We call the function directly after patching streamlit and DB calls.
    This avoids AppTest's widget-value initialisation quirks when the
    ``value=`` argument comes from a mocked DB object.
    """

    def _run(self, mocker, *, profile):
        """Patch everything and call render_profile_page()."""
        import streamlit as st

        st.session_state["user_id"] = 1

        mocker.patch("src.app.get_session", return_value=_session_ctx())
        mocker.patch("src.app.get_profile", return_value=profile)
        mocker.patch("streamlit.title")
        mocker.patch("streamlit.subheader")
        mocker.patch("streamlit.divider")
        mocker.patch("streamlit.caption")
        mocker.patch("streamlit.info")
        mocker.patch("streamlit.success")
        mocker.patch("streamlit.error")
        mock_toggle = mocker.patch("streamlit.toggle", return_value=False)
        mock_text_input = mocker.patch(
            "streamlit.text_input",
            side_effect=lambda label, value="", **kw: value,
        )
        mocker.patch("streamlit.file_uploader", return_value=None)
        mocker.patch("streamlit.button", return_value=False)

        from src.app import render_profile_page

        render_profile_page()
        return mock_toggle, mock_text_input

    def test_existing_profile_prepopulates_fields(self, mocker):
        """Existing profile → text_input called with pre-populated values.

        Requirement 3.7
        """
        profile = _make_complete_profile_mock()

        _, mock_text_input = self._run(mocker, profile=profile)

        calls = mock_text_input.call_args_list
        # Find the Target Role call
        role_calls = [c for c in calls if c.args and c.args[0] == "Target Role"]
        assert role_calls, "text_input('Target Role', ...) was not called"
        assert role_calls[0].kwargs.get("value") == "Software Engineer"

        # Find the Recipient Email call
        email_calls = [c for c in calls if c.args and c.args[0] == "Recipient Email"]
        assert email_calls, "text_input('Recipient Email', ...) was not called"
        assert email_calls[0].kwargs.get("value") == "r@example.com"

    def test_incomplete_profile_hides_schedule_toggle(self, mocker):
        """Incomplete profile → st.toggle NOT called.

        Requirement 4.1
        """
        profile = _make_incomplete_profile_mock()

        mock_toggle, _ = self._run(mocker, profile=profile)

        mock_toggle.assert_not_called()

    def test_complete_profile_shows_schedule_toggle(self, mocker):
        """Complete profile → st.toggle IS called.

        Requirement 4.1
        """
        profile = _make_complete_profile_mock()

        mock_toggle, _ = self._run(mocker, profile=profile)

        mock_toggle.assert_called_once()

    def test_no_profile_shows_empty_fields(self, mocker):
        """No existing profile → text_input called with empty string values."""
        _, mock_text_input = self._run(mocker, profile=None)

        calls = mock_text_input.call_args_list
        role_calls = [c for c in calls if c.args and c.args[0] == "Target Role"]
        assert role_calls, "text_input('Target Role', ...) was not called"
        assert role_calls[0].kwargs.get("value") == ""


# ---------------------------------------------------------------------------
# render_search_page — direct call tests
# ---------------------------------------------------------------------------


class TestSearchPageDirect:
    """Direct-call tests for render_search_page.

    Requirement 6.1, 6.3
    """

    def _setup(self, mocker, *, profile):
        import streamlit as st

        st.session_state["user_id"] = 1

        mocker.patch("src.app.get_session", return_value=_session_ctx())
        mocker.patch("src.app.get_profile", return_value=profile)
        mocker.patch("streamlit.title")
        mocker.patch("streamlit.subheader")
        mocker.patch("streamlit.info")
        mock_warning = mocker.patch("streamlit.warning")
        mocker.patch("streamlit.error")
        mocker.patch("streamlit.button", return_value=False)
        mock_pipeline = mocker.patch("src.app._run_pipeline_for_user")
        mock_save = mocker.patch("src.app.save_job_result")
        return mock_warning, mock_pipeline, mock_save

    def test_incomplete_profile_shows_warning_no_pipeline(self, mocker):
        """Incomplete profile → st.warning called, pipeline NOT invoked.

        Requirement 6.1
        """
        profile = _make_incomplete_profile_mock()
        mock_warning, mock_pipeline, _ = self._setup(mocker, profile=profile)

        from src.app import render_search_page

        render_search_page()

        mock_warning.assert_called_once()
        mock_pipeline.assert_not_called()

    def test_none_profile_shows_warning_no_pipeline(self, mocker):
        """None profile → st.warning called, pipeline NOT invoked.

        Requirement 6.1
        """
        mock_warning, mock_pipeline, _ = self._setup(mocker, profile=None)

        from src.app import render_search_page

        render_search_page()

        mock_warning.assert_called_once()
        mock_pipeline.assert_not_called()

    def test_empty_pipeline_result_does_not_save(self, mocker):
        """Empty pipeline result → save_job_result NOT called.

        Requirement 6.3 (only save when non-empty)
        """
        profile = _make_complete_profile_mock()

        import streamlit as st

        st.session_state["user_id"] = 1

        mocker.patch("src.app.get_session", return_value=_session_ctx())
        mocker.patch("src.app.get_profile", return_value=profile)
        mocker.patch("streamlit.title")
        mocker.patch("streamlit.subheader")
        mocker.patch("streamlit.info")
        mocker.patch("streamlit.warning")
        mocker.patch("streamlit.error")
        # Simulate "Search Now" button clicked
        mocker.patch("streamlit.button", return_value=True)
        mocker.patch("streamlit.spinner", return_value=_session_ctx())
        mocker.patch("src.app._run_pipeline_for_user", return_value=pd.DataFrame())
        mock_save = mocker.patch("src.app.save_job_result")

        from src.app import render_search_page

        render_search_page()

        mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# render_history_page — direct call tests
# ---------------------------------------------------------------------------


class TestHistoryPageDirect:
    """Direct-call tests for render_history_page.

    Requirement 7.2, 7.3
    """

    def test_no_results_calls_st_info(self, mocker):
        """No stored results → st.info called with 'No results recorded yet.'

        Requirement 7.3
        """
        import streamlit as st

        st.session_state["user_id"] = 1

        mocker.patch("src.app.get_session", return_value=_session_ctx())
        mocker.patch("src.app.get_user_results", return_value=[])
        mocker.patch("streamlit.title")
        mock_info = mocker.patch("streamlit.info")

        from src.app import render_history_page

        render_history_page()

        mock_info.assert_called_once()
        assert "No results recorded yet" in mock_info.call_args[0][0]

    def test_results_render_expanders(self, mocker):
        """Past results → st.expander called once per result.

        Requirement 7.2
        """
        import streamlit as st

        st.session_state["user_id"] = 1

        mock_result = MagicMock()
        mock_result.run_date = date(2024, 6, 1)
        mock_result.to_dataframe.return_value = _minimal_df()

        mocker.patch("src.app.get_session", return_value=_session_ctx())
        mocker.patch("src.app.get_user_results", return_value=[mock_result])
        mocker.patch("streamlit.title")
        mocker.patch("streamlit.info")
        mocker.patch("streamlit.dataframe")
        mock_expander = mocker.patch("streamlit.expander", return_value=_session_ctx())

        from src.app import render_history_page

        render_history_page()

        mock_expander.assert_called_once()
        label = mock_expander.call_args[0][0]
        assert "2024-06-01" in label
