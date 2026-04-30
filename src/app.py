"""Multi-page Streamlit application for the Job Finder platform.

Pages
-----
- Login / Register  (unauthenticated)
- Profile           (authenticated)
- Search            (authenticated)
- History           (authenticated)

Startup sequence
----------------
1. init_db()       — create SQLite tables if they don't exist
2. get_scheduler() — start APScheduler background scheduler (once, via cache_resource)
3. Route to the correct page based on st.session_state.user_id
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import streamlit as st
from sqlalchemy.exc import IntegrityError

try:
    # When run via pytest from the project root, src is a package
    from src.auth import hash_password, validate_email, validate_password, verify_password
    from src.cv_parser import extract_cv_text
    from src.database import (
        get_profile,
        get_session,
        get_user_by_email,
        get_user_results,
        init_db,
        save_job_result,
        set_schedule_enabled,
        upsert_profile,
        create_user,
    )
    from src.email_sender import send_results_email
    from src.job_scheduler import _run_pipeline_for_user, get_scheduler
except ModuleNotFoundError:
    # When run via `streamlit run src/app.py`, src/ is on sys.path directly
    from auth import hash_password, validate_email, validate_password, verify_password  # type: ignore[no-redef]
    from cv_parser import extract_cv_text  # type: ignore[no-redef]
    from database import (  # type: ignore[no-redef]
        get_profile,
        get_session,
        get_user_by_email,
        get_user_results,
        init_db,
        save_job_result,
        set_schedule_enabled,
        upsert_profile,
        create_user,
    )
    from email_sender import send_results_email  # type: ignore[no-redef]
    from job_scheduler import _run_pipeline_for_user, get_scheduler  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

st.set_page_config(page_title="Job Finder", layout="wide")

# ---------------------------------------------------------------------------
# Column config for job URL links
# ---------------------------------------------------------------------------
_LINK_COL_CONFIG = {"job_url": st.column_config.LinkColumn("job_url")}


# ---------------------------------------------------------------------------
# 6.2  Login / Register page
# ---------------------------------------------------------------------------


def render_login_register_page() -> None:
    """Shown when no user is authenticated.  Contains Login and Register tabs.

    Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4
    """
    st.title("Job Finder")

    login_tab, register_tab = st.tabs(["Login", "Register"])

    # ------------------------------------------------------------------
    # Login tab
    # ------------------------------------------------------------------
    with login_tab:
        st.subheader("Log in to your account")
        login_email = st.text_input("Email", key="login_email")
        login_password = st.text_input("Password", type="password", key="login_password")

        if st.button("Log In", key="login_btn"):
            with get_session() as session:
                user = get_user_by_email(login_email, session)
                # Extract plain values before session closes
                if user is not None:
                    user_id = user.id
                    user_email = user.email
                    user_hashed_password = user.hashed_password
                else:
                    user_id = None
                    user_email = None
                    user_hashed_password = None

            # Same error message for unknown email and wrong password — no enumeration
            if user_hashed_password is None or not verify_password(login_password, user_hashed_password):
                st.error("Invalid credentials.")
            else:
                st.session_state.user_id = user_id
                st.session_state.user_email = user_email
                st.rerun()

    # ------------------------------------------------------------------
    # Register tab
    # ------------------------------------------------------------------
    with register_tab:
        st.subheader("Create a new account")
        reg_email = st.text_input("Email", key="reg_email")
        reg_password = st.text_input("Password (min 8 characters)", type="password", key="reg_password")

        if st.button("Create Account", key="register_btn"):
            # Client-side validation before hitting the DB
            if not validate_email(reg_email):
                st.error("Invalid email address.")
            elif not validate_password(reg_password):
                st.error("Password must be at least 8 characters.")
            else:
                try:
                    with get_session() as session:
                        user = create_user(reg_email, hash_password(reg_password), session)
                        # Extract before session closes
                        new_user_id = user.id
                        new_user_email = user.email
                except IntegrityError:
                    st.error("Email already registered.")
                else:
                    # Auto-login on successful registration
                    st.session_state.user_id = new_user_id
                    st.session_state.user_email = new_user_email
                    st.rerun()


# ---------------------------------------------------------------------------
# 6.3  Profile page
# ---------------------------------------------------------------------------


def render_profile_page() -> None:
    """Profile settings: target role, CV upload, recipient email, schedule toggle.

    Requirements: 3.1–3.7, 4.1–4.5
    """
    st.title("My Profile")

    user_id: int = st.session_state.user_id

    # Load existing profile and immediately extract plain values before session closes
    with get_session() as session:
        profile = get_profile(user_id, session)
        if profile is not None:
            existing_role = profile.target_role or ""
            existing_email = profile.recipient_email or ""
            existing_cv_text = profile.cv_text or ""
            existing_uk_locations = profile.uk_locations or "London"
            existing_intl_locations = profile.international_locations or ""
            schedule_enabled = profile.schedule_enabled
            profile_is_complete = profile.is_complete
        else:
            existing_role = ""
            existing_email = ""
            existing_cv_text = ""
            existing_uk_locations = "London"
            existing_intl_locations = ""
            schedule_enabled = False
            profile_is_complete = False

    # ------------------------------------------------------------------
    # Profile form
    # ------------------------------------------------------------------
    st.subheader("Profile Settings")

    target_role = st.text_input("Target Role", value=existing_role)
    cv_file = st.file_uploader(
        "Upload CV (PDF or DOCX)",
        type=["pdf", "docx"],
        help="Upload a new CV to replace the stored one, or leave blank to keep the existing CV.",
    )
    recipient_email = st.text_input("Recipient Email", value=existing_email)

    st.divider()
    st.subheader("📍 Job Search Locations")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**🇬🇧 UK Cities**")
        uk_locations_input = st.text_input(
            "UK cities (comma-separated)",
            value=existing_uk_locations,
            label_visibility="collapsed",
            placeholder="e.g. London, Manchester",
        )
        st.caption("Only shows companies with UK visa sponsorship")
    with col2:
        st.markdown("**🌍 International Cities** *(optional)*")
        intl_locations_input = st.text_input(
            "International cities (comma-separated)",
            value=existing_intl_locations,
            label_visibility="collapsed",
            placeholder="e.g. Amsterdam Netherlands, Berlin Germany",
        )
        st.caption("Shows all companies — no sponsorship filter")

    if existing_cv_text:
        st.caption("✅ A CV is already stored for your profile.")

    if st.button("Save Profile"):
        # Validate target role
        if not target_role.strip():
            st.error("Target role is required.")
            return

        # Resolve CV text: use uploaded file or fall back to stored text
        if cv_file is not None:
            cv_text = extract_cv_text(cv_file.read(), cv_file.name)
            if not cv_text.strip():
                st.error("Could not extract text from CV.")
                return
        elif existing_cv_text:
            cv_text = existing_cv_text
        else:
            st.error("A CV is required.")
            return

        # Validate recipient email
        if not validate_email(recipient_email):
            st.error("Invalid recipient email address.")
            return

        with get_session() as session:
            upsert_profile(
                user_id=user_id,
                target_role=target_role.strip(),
                cv_text=cv_text,
                recipient_email=recipient_email.strip(),
                uk_locations=uk_locations_input.strip() or "London",
                international_locations=intl_locations_input.strip(),
                session=session,
            )

        st.success("Profile saved.")
        # Reload profile values — extract immediately before session closes
        with get_session() as session:
            profile = get_profile(user_id, session)
            if profile is not None:
                existing_cv_text = profile.cv_text or ""
                existing_uk_locations = profile.uk_locations or "London"
                existing_intl_locations = profile.international_locations or ""
                schedule_enabled = profile.schedule_enabled
                profile_is_complete = profile.is_complete
            else:
                existing_cv_text = ""
                existing_uk_locations = "London"
                existing_intl_locations = ""
                schedule_enabled = False
                profile_is_complete = False

    # ------------------------------------------------------------------
    # Schedule toggle — only shown when profile is complete (Requirement 4.1)
    # ------------------------------------------------------------------
    if profile_is_complete:
        st.divider()
        st.subheader("Daily Digest Schedule")
        new_schedule = st.toggle(
            "Send me daily results at 9am UTC",
            value=schedule_enabled,
            key="schedule_toggle",
        )
        if new_schedule != schedule_enabled:
            try:
                with get_session() as session:
                    set_schedule_enabled(user_id, new_schedule, session)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    else:
        st.info(
            "Complete your profile (target role, CV, and recipient email) "
            "to enable the daily digest schedule."
        )


# ---------------------------------------------------------------------------
# 6.4  Search page
# ---------------------------------------------------------------------------


def render_search_page() -> None:
    """Manual job search trigger, results display, and email option.

    Requirements: 6.1–6.5
    """
    st.title("Search Jobs")

    user_id: int = st.session_state.user_id

    # Extract all needed profile values inside the session
    with get_session() as session:
        profile = get_profile(user_id, session)
        if profile is not None:
            profile_is_complete = profile.is_complete
            profile_target_role = profile.target_role or ""
            profile_cv_text = profile.cv_text or ""
            profile_recipient_email = profile.recipient_email or ""
            profile_uk_locations = profile.uk_locations or "London"
            profile_intl_locations = profile.international_locations or ""
            profile_uk_location_list = profile.uk_location_list
            profile_intl_location_list = profile.international_location_list
        else:
            profile_is_complete = False
            profile_target_role = ""
            profile_cv_text = ""
            profile_recipient_email = ""
            profile_uk_locations = "London"
            profile_intl_locations = ""
            profile_uk_location_list = ["London"]
            profile_intl_location_list = []

    # Redirect to Profile page if profile is incomplete (Requirement 6.1)
    if not profile_is_complete:
        st.warning(
            "Your profile is incomplete. Please set your target role, upload a CV, "
            "and provide a recipient email before searching."
        )
        return

    if st.button("Search Now"):
        with st.spinner("Running job search pipeline…"):
            try:
                # Build a plain namespace so _run_pipeline_for_user gets the fields it needs
                class _ProfileProxy:
                    target_role = profile_target_role
                    cv_text = profile_cv_text
                    recipient_email = profile_recipient_email
                    user_id = user_id
                    uk_locations = profile_uk_locations
                    international_locations = profile_intl_locations

                    @property
                    def uk_location_list(self):
                        return profile_uk_location_list

                    @property
                    def international_location_list(self):
                        return profile_intl_location_list

                scored_df = _run_pipeline_for_user(_ProfileProxy())
            except Exception as exc:
                st.error(f"Search failed: {exc}")
                return

        if scored_df.empty:
            st.info("No matching jobs found for your profile.")
            return

        # Persist results automatically (Requirement 6.3)
        run_date = datetime.now(timezone.utc).date()
        with get_session() as session:
            save_job_result(user_id, run_date, scored_df, session)

        st.session_state["last_search_df"] = scored_df
        st.session_state["last_search_recipient"] = profile_recipient_email

    # Display results if available in session state
    results_df = st.session_state.get("last_search_df")
    if results_df is not None and not results_df.empty:
        st.subheader(f"Results — {len(results_df)} job(s) found")
        st.dataframe(results_df, use_container_width=True, column_config=_LINK_COL_CONFIG)

        # Email Results button (Requirement 6.4)
        if st.button("Email Results"):
            recipient = st.session_state.get("last_search_recipient", "")
            try:
                send_results_email(
                    df=results_df,
                    run_date=datetime.now(timezone.utc),
                    summary={
                        "jobs_scored": len(results_df),
                        "new_jobs_added": len(results_df),
                        "total_in_csv": len(results_df),
                    },
                    recipient=recipient,
                )
                st.success(f"Results emailed to {recipient}.")
            except Exception as exc:
                st.error(f"Failed to send email: {exc}")


# ---------------------------------------------------------------------------
# 6.5  History page
# ---------------------------------------------------------------------------


def render_history_page() -> None:
    """Past job search results, ordered newest first.

    Requirements: 7.1–7.4
    """
    st.title("Search History")

    user_id: int = st.session_state.user_id

    # Extract all data inside the session before it closes
    with get_session() as session:
        results = get_user_results(user_id, session)
        result_data = [(r.run_date, r.to_dataframe()) for r in results]

    if not result_data:
        st.info("No results recorded yet.")
        return

    for run_date, df in result_data:
        with st.expander(f"Run: {run_date}"):
            st.dataframe(df, use_container_width=True, column_config=_LINK_COL_CONFIG)


# ---------------------------------------------------------------------------
# 6.1  main() — startup and routing
# ---------------------------------------------------------------------------


def main() -> None:
    """Application entry point.

    1. Initialise the database (idempotent).
    2. Start the background scheduler (cached — runs once per process).
    3. Initialise session state keys.
    4. Route to the correct page.

    Requirements: 2.6, 2.7, 5.1
    """
    # Startup
    init_db()
    get_scheduler()

    # Initialise session state
    if "user_id" not in st.session_state:
        st.session_state.user_id = None
    if "user_email" not in st.session_state:
        st.session_state.user_email = None

    # Unauthenticated — show login/register
    if st.session_state.user_id is None:
        render_login_register_page()
        return

    # Authenticated — sidebar navigation + logout
    with st.sidebar:
        st.write(f"Logged in as **{st.session_state.user_email}**")
        page = st.radio(
            "Navigate",
            ["Profile", "Search", "History"],
            key="nav_page",
        )
        if st.button("Logout"):
            st.session_state.user_id = None
            st.session_state.user_email = None
            # Clear any search results from session state on logout
            st.session_state.pop("last_search_df", None)
            st.session_state.pop("last_search_recipient", None)
            st.rerun()

    if page == "Profile":
        render_profile_page()
    elif page == "Search":
        render_search_page()
    elif page == "History":
        render_history_page()


if __name__ == "__main__":
    main()
else:
    # Streamlit runs the module at top level, so call main() directly.
    main()
