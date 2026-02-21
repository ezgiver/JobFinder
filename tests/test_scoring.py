"""Tests for AI job scoring pipeline."""

import json
from unittest.mock import MagicMock, patch

import pandas as pd

from src.scoring import GEMINI_DELAY_SECONDS, score_jobs


def _mock_client(responses):
    """Build a mock Gemini client returning given JSON dicts in sequence."""
    client = MagicMock()
    side_effects = []
    for resp in responses:
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(resp)
        side_effects.append(mock_resp)
    client.models.generate_content.side_effect = side_effects
    return client


class TestScoreJobs:
    """Bugs here either send wrong data to Gemini, misparse the response,
    crash on unexpected API output, or skip rate limiting."""

    def test_prompt_contains_cv_and_job_description(self):
        """If the prompt template is broken, Gemini scores against garbage."""
        df = pd.DataFrame({"description": ["Build microservices in Go"]})
        client = _mock_client([{"match_score": 75, "reasoning": "OK"}])

        with patch("src.scoring.time.sleep"):
            score_jobs("I know Python and AWS", df, client)

        actual_prompt = client.models.generate_content.call_args[1]["contents"]
        assert "I know Python and AWS" in actual_prompt
        assert "Build microservices in Go" in actual_prompt

    def test_scores_assigned_to_correct_rows(self):
        """The most dangerous bug: assigning Job A's score to Job B."""
        df = pd.DataFrame(
            {"description": ["Python backend role", "Java frontend role"]}
        )
        client = _mock_client(
            [
                {"match_score": 92, "reasoning": "Python expert"},
                {"match_score": 30, "reasoning": "No Java skills"},
            ]
        )

        with patch("src.scoring.time.sleep"):
            result = score_jobs("Python dev CV", df, client)

        assert result.iloc[0]["match_score"] == 92
        assert result.iloc[0]["reasoning"] == "Python expert"
        assert result.iloc[1]["match_score"] == 30
        assert result.iloc[1]["reasoning"] == "No Java skills"

    def test_none_description_treated_as_empty(self):
        """jobspy returns None for description sometimes. Must not crash."""
        df = pd.DataFrame({"description": [None]})
        client = MagicMock()

        with patch("src.scoring.time.sleep"):
            result = score_jobs("CV", df, client)

        assert result["match_score"].iloc[0] == 0
        assert result["reasoning"].iloc[0] == "No job description available."
        client.models.generate_content.assert_not_called()

    def test_gemini_returns_invalid_json(self):
        """Gemini ignoring response_schema must not crash the pipeline."""
        df = pd.DataFrame({"description": ["A real job"]})
        client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "This is not JSON at all"
        client.models.generate_content.return_value = mock_resp

        with patch("src.scoring.time.sleep"):
            result = score_jobs("CV", df, client)

        assert result["match_score"].iloc[0] == 0
        assert "Scoring failed" in result["reasoning"].iloc[0]

    def test_gemini_returns_unexpected_keys(self):
        """Valid JSON but wrong key names should fail gracefully."""
        df = pd.DataFrame({"description": ["A job"]})
        client = _mock_client([{"score": 80, "reason": "Good"}])

        with patch("src.scoring.time.sleep"):
            result = score_jobs("CV", df, client)

        assert result["match_score"].iloc[0] == 0
        assert "Scoring failed" in result["reasoning"].iloc[0]

    def test_rate_limit_delay_value_is_correct(self):
        """Wrong delay value = getting blocked by Gemini."""
        df = pd.DataFrame({"description": ["Job A", "Job B"]})
        client = _mock_client(
            [
                {"match_score": 80, "reasoning": "A"},
                {"match_score": 70, "reasoning": "B"},
            ]
        )

        with patch("src.scoring.time.sleep") as mock_sleep:
            score_jobs("CV", df, client)

        mock_sleep.assert_called_once_with(GEMINI_DELAY_SECONDS)

    def test_no_sleep_after_last_job(self):
        """No wasted time sleeping after the final job."""
        df = pd.DataFrame({"description": ["Only job"]})
        client = _mock_client([{"match_score": 50, "reasoning": "OK"}])

        with patch("src.scoring.time.sleep") as mock_sleep:
            score_jobs("CV", df, client)

        mock_sleep.assert_not_called()

    def test_does_not_mutate_input_dataframe(self):
        """Mutating input df corrupts data shown in the 'All jobs' expander."""
        df = pd.DataFrame({"description": ["A job"], "title": ["Engineer"]})
        original_cols = list(df.columns)
        client = _mock_client([{"match_score": 50, "reasoning": "OK"}])

        with patch("src.scoring.time.sleep"):
            result = score_jobs("CV", df, client)

        assert list(df.columns) == original_cols
        assert "match_score" not in df.columns
        assert "match_score" in result.columns

    def test_partial_failure_doesnt_corrupt_alignment(self):
        """Job 2 of 3 fails — scores must still align to the right rows."""
        df = pd.DataFrame(
            {"description": ["Good job", "Crash job", "Another good job"]}
        )
        client = MagicMock()
        resp_ok1 = MagicMock()
        resp_ok1.text = json.dumps({"match_score": 85, "reasoning": "Great"})
        resp_fail = MagicMock()
        resp_fail.text = "NOT JSON"
        resp_ok2 = MagicMock()
        resp_ok2.text = json.dumps({"match_score": 60, "reasoning": "Decent"})
        client.models.generate_content.side_effect = [resp_ok1, resp_fail, resp_ok2]

        with patch("src.scoring.time.sleep"):
            result = score_jobs("CV", df, client)

        assert result.iloc[0]["match_score"] == 85
        assert result.iloc[1]["match_score"] == 0
        assert "Scoring failed" in result.iloc[1]["reasoning"]
        assert result.iloc[2]["match_score"] == 60

    def test_progress_callback_called(self):
        """Progress callback must be invoked for each job."""
        df = pd.DataFrame({"description": ["Job A", "Job B"]})
        client = _mock_client(
            [
                {"match_score": 80, "reasoning": "A"},
                {"match_score": 70, "reasoning": "B"},
            ]
        )
        callback = MagicMock()

        with patch("src.scoring.time.sleep"):
            score_jobs("CV", df, client, progress_callback=callback)

        assert callback.call_count == 2
        callback.assert_any_call(1, 2)
        callback.assert_any_call(2, 2)

    def test_curly_braces_in_description_dont_crash(self):
        """Job descriptions with code snippets like function() { ... } must
        not crash prompt building. This was a real bug with str.format()."""
        df = pd.DataFrame(
            {"description": ["Required: JavaScript function() { return true; }"]}
        )
        client = _mock_client([{"match_score": 60, "reasoning": "JS role"}])

        with patch("src.scoring.time.sleep"):
            result = score_jobs("Python dev CV", df, client)

        assert result["match_score"].iloc[0] == 60
        # Verify the curly braces made it into the prompt intact
        actual_prompt = client.models.generate_content.call_args[1]["contents"]
        assert "function() { return true; }" in actual_prompt

    def test_missing_description_column(self):
        """If jobspy changes column names, score_jobs must not crash."""
        df = pd.DataFrame({"title": ["Engineer"], "company": ["Acme"]})
        client = MagicMock()

        with patch("src.scoring.time.sleep"):
            result = score_jobs("CV", df, client)

        assert result["match_score"].iloc[0] == 0
        assert "No description column" in result["reasoning"].iloc[0]
        client.models.generate_content.assert_not_called()
