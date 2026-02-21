"""Streamlit UI for the Job Finder application."""

import os

import pandas as pd
import streamlit as st
from google import genai
from jobspy import scrape_jobs

from cv_parser import extract_cv_text
from scoring import score_jobs
from sponsors import load_sponsor_register, verify_sponsors

st.set_page_config(page_title="Job Finder", layout="wide")

MIN_MATCH_SCORE = 70
MAX_RESULTS = 100

DISPLAY_COLS = [
    "match_score",
    "reasoning",
    "title",
    "company",
    "location",
    "job_url",
    "date_posted",
]


def reorder_cols(df: pd.DataFrame) -> pd.DataFrame:
    front = [c for c in DISPLAY_COLS if c in df.columns]
    rest = [c for c in df.columns if c not in front]
    return df[front + rest]


LINK_COL_CONFIG = {"job_url": st.column_config.LinkColumn("job_url")}

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("Job Search Automation")

env_api_key = os.environ.get("GEMINI_API_KEY", "").strip().strip("\"'")

with st.form("search_form"):
    gemini_api_key = st.text_input(
        "Gemini API Key",
        value=env_api_key,
        type="password",
        placeholder="Paste your Gemini API key here",
    )
    target_role = st.text_input(
        "Target Role", placeholder="e.g. Software Engineer, Data Analyst"
    )
    cv_file = st.file_uploader("Upload your CV", type=["pdf", "docx"])
    days_old = st.number_input(
        "Days Old (filter jobs posted in the last X days)",
        min_value=1,
        value=7,
        step=1,
    )
    submitted = st.form_submit_button("Search Jobs")

if submitted:
    gemini_api_key = gemini_api_key.strip()
    if not gemini_api_key:
        st.error("Please enter your Gemini API key.")
    elif not target_role:
        st.error("Please enter a target role.")
    elif not cv_file:
        st.error("Please upload your CV.")
    else:
        cv_text = extract_cv_text(cv_file)
        if not cv_text.strip():
            st.error("Could not extract text from your CV. Please check the file.")
            st.stop()

        # Step 1: Load sponsor register
        with st.spinner("Loading UK sponsor register..."):
            sponsors = load_sponsor_register()
        if not sponsors:
            st.error("Could not load the UK sponsor register. Gov.uk may be down.")
            st.stop()

        st.info(f"Loaded {len(sponsors):,} sponsored companies from the UK register.")

        # Step 2: Scrape jobs by target role in London
        hours_old = days_old * 24
        with st.spinner(f"Searching for '{target_role}' jobs in London..."):
            try:
                master_df = scrape_jobs(
                    site_name=["indeed", "linkedin"],
                    search_term=target_role,
                    location="London, UK",
                    hours_old=hours_old,
                    results_wanted=MAX_RESULTS,
                    linkedin_fetch_description=True,
                )
            except Exception as e:
                st.error(f"Scraping failed: {e}")
                st.stop()

        if master_df.empty:
            st.info(f"No '{target_role}' jobs found in London.")
            st.stop()

        master_df = master_df.drop_duplicates(subset="job_url", keep="first")
        st.info(f"Scraped {len(master_df)} unique jobs.")

        # Step 3: Fuzzy-match against visa sponsor register
        with st.spinner("Verifying companies against sponsor register..."):
            sponsor_check = verify_sponsors(master_df["company"], sponsors)
        master_df["verified_sponsor"] = sponsor_check["verified_sponsor"]
        sponsor_df = master_df[master_df["verified_sponsor"]].copy()

        if sponsor_df.empty:
            st.warning("None of the scraped jobs are from verified UK visa sponsors.")
            with st.expander(f"All scraped jobs ({len(master_df)} total)"):
                st.dataframe(master_df, use_container_width=True)
            st.stop()

        st.info(
            f"{len(sponsor_df)} jobs are from verified visa sponsors. Now scoring with AI..."
        )

        # Step 4: Score sponsor jobs against CV
        gemini_client = genai.Client(api_key=gemini_api_key)
        progress_bar = st.progress(0, text="Scoring jobs with AI...")

        def _update_progress(current, total):
            progress_bar.progress(current / total, text=f"Scoring job {current}/{total}")

        scored_df = score_jobs(cv_text, sponsor_df, gemini_client, _update_progress)
        progress_bar.empty()

        scored_df = scored_df.sort_values("match_score", ascending=False)
        matched_df = scored_df[scored_df["match_score"] >= MIN_MATCH_SCORE]

        if not matched_df.empty:
            st.subheader(f"{len(matched_df)} jobs match your CV (score >= {MIN_MATCH_SCORE})")
            st.dataframe(
                reorder_cols(matched_df),
                use_container_width=True,
                column_config=LINK_COL_CONFIG,
            )
            csv_data = matched_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download matched jobs as CSV",
                data=csv_data,
                file_name="matched_jobs.csv",
                mime="text/csv",
            )
        else:
            st.warning(f"No jobs scored {MIN_MATCH_SCORE} or above.")

        with st.expander(f"All sponsor jobs ({len(scored_df)} total)"):
            st.dataframe(
                reorder_cols(scored_df),
                use_container_width=True,
                column_config=LINK_COL_CONFIG,
            )
            all_csv = scored_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download all sponsor jobs as CSV",
                data=all_csv,
                file_name="all_sponsor_jobs.csv",
                mime="text/csv",
            )
