"""
Pytest configuration for the job-finder test suite.

Sets BCRYPT_LOG_ROUNDS=4 before any module is imported so that bcrypt
operations in property-based tests complete in milliseconds rather than
seconds. Production code uses the default of 12 rounds.
"""

import os

import pytest

# Must be set before src.auth is imported so the module-level constant picks
# it up. pytest loads conftest.py before collecting test modules.
os.environ.setdefault("BCRYPT_LOG_ROUNDS", "4")


@pytest.fixture(autouse=True)
def _reset_streamlit_session_state():
    """Clear Streamlit session state before each test.

    Prevents state set by direct ``st.session_state`` manipulation in one
    test (e.g. ``user_id``) from leaking into subsequent AppTest runs.
    """
    try:
        import streamlit as st
        st.session_state.clear()
    except Exception:
        pass
    yield
    try:
        import streamlit as st
        st.session_state.clear()
    except Exception:
        pass
