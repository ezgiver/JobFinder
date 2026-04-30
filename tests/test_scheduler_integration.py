"""Integration test for the full scheduler flow — Requirements 2.3, 3.1, 5.1, 5.2, 5.3, 6.3."""

import pandas as pd
import pytest

import src.scheduler as sched
import src.result_store as result_store


def test_full_scheduler_flow(tmp_path, monkeypatch):
    """Full integration: scrape → sponsor-verify → score → persist → config update."""

    # --- 1. Create a temp CV file ---
    cv_file = tmp_path / "cv.pdf"
    cv_file.write_bytes(b"%PDF-1.4 fake pdf content")

    # --- 2. Config with next_run_timestamp in the past ---
    config = {
        "target_role": "Software Engineer",
        "cv_path": str(cv_file),
        "run_interval_days": 1,
        "iteration_count": 1,
        "completed_iterations": 0,
        "next_run_timestamp": "2020-01-01T00:00:00Z",
    }

    # --- 3. Two scraped jobs; only "Acme Corp" matches the sponsor register ---
    scraped_df = pd.DataFrame(
        [
            {
                "job_url": "https://example.com/job/1",
                "title": "Software Engineer",
                "company": "Acme Corp",
                "location": "London",
                "date_posted": "2024-01-01",
                "description": "Great role at Acme.",
            },
            {
                "job_url": "https://example.com/job/2",
                "title": "Data Analyst",
                "company": "Unknown Ltd",
                "location": "London",
                "date_posted": "2024-01-02",
                "description": "Data role at Unknown.",
            },
        ]
    )

    # --- 4. Sponsor register returns one matching company ---
    sponsor_register = (["Acme Corp"],)  # tuple with one element (list of names)

    # --- 5. verify_sponsors returns a full-length result aligned with scraped_df ---
    # The scheduler does: jobs_df["verified_sponsor"] = sponsor_results["verified_sponsor"]
    # so the returned DataFrame must have the same index as scraped_df.
    sponsor_results_df = pd.DataFrame(
        {
            "verified_sponsor": [True, False],
            "sponsor_match_score": [100, 0],
        },
        index=scraped_df.index,
    )

    # --- 6. score_jobs returns the verified job with score + reasoning ---
    verified_job = scraped_df[scraped_df["company"] == "Acme Corp"].copy()
    verified_job["verified_sponsor"] = True
    verified_job["sponsor_match_score"] = 100
    scored_df = verified_job.copy()
    scored_df["match_score"] = 85
    scored_df["reasoning"] = "Good match"
    scored_df["match_tier"] = "Strong Match"
    scored_df["run_timestamp"] = "2024-01-01T00:00:00Z"

    # --- 7. Patch cumulative CSV path ---
    csv_path = tmp_path / "results.csv"
    monkeypatch.setattr(sched, "CUMULATIVE_CSV_PATH", csv_path)
    monkeypatch.setattr(result_store, "CUMULATIVE_CSV_PATH", csv_path)

    # --- 8. Patch all external dependencies ---
    saved_configs = []

    monkeypatch.setattr(sched, "load_config", lambda: config)
    monkeypatch.setattr(sched, "save_config", lambda cfg: saved_configs.append(dict(cfg)))
    monkeypatch.setattr(sched, "load_sponsor_register", lambda: sponsor_register)
    monkeypatch.setattr(sched, "verify_sponsors", lambda companies, sponsors: sponsor_results_df)
    monkeypatch.setattr(sched, "score_jobs", lambda cv_text, df, client: scored_df)

    # Patch merge_results to use the tmp csv_path (default arg is bound at import time)
    monkeypatch.setattr(sched, "merge_results", lambda df: result_store.merge_results(df, csv_path))

    # Patch jobspy.scrape_jobs inside the scheduler's run_pipeline
    import jobspy
    monkeypatch.setattr(jobspy, "scrape_jobs", lambda **kwargs: scraped_df)

    # Patch cv_parser so we don't need a real PDF
    monkeypatch.setattr(sched, "extract_cv_text_from_path", lambda path: "Experienced software engineer.")

    # Patch genai.Client so no real API call is made
    class FakeClient:
        pass

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", lambda api_key: FakeClient())

    # Set dummy API key
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-testing")

    # --- 9. Run main() and expect clean exit ---
    with pytest.raises(SystemExit) as exc_info:
        sched.main()

    assert exc_info.value.code == 0

    # --- 10. Assert cumulative CSV was created ---
    assert csv_path.exists(), "Cumulative CSV should have been created"

    result = pd.read_csv(csv_path)

    # --- 11. Assert it contains the expected row ---
    assert len(result) >= 1
    assert "https://example.com/job/1" in result["job_url"].values, (
        "Verified job URL should be present in the CSV"
    )

    # --- 12. Assert completed_iterations was incremented ---
    assert len(saved_configs) >= 1
    final_config = saved_configs[-1]
    assert final_config["completed_iterations"] == 1, (
        f"Expected completed_iterations=1, got {final_config['completed_iterations']}"
    )
