"""Tests for structured CV profile extraction."""

import json
from unittest.mock import MagicMock

import pytest

from src.cv_profile import PROFILE_SCHEMA, extract_cv_profile

VALID_PROFILE = {
    "skills": [
        {"name": "Python", "proficiency": "expert"},
        {"name": "AWS", "proficiency": "advanced"},
    ],
    "seniority_level": "senior",
    "total_years_experience": 8,
    "industries": ["fintech", "e-commerce"],
    "education": {"degree_level": "MSc", "field": "Computer Science"},
    "job_titles": ["Senior Software Engineer", "Software Engineer"],
}

SAMPLE_CV_TEXT = "Jane Doe — 8 years Python, AWS, fintech background."


def _mock_client(response_text):
    """Build a mock Gemini client returning the given text."""
    client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = response_text
    client.models.generate_content.return_value = mock_resp
    return client


class TestExtractCvProfile:
    def test_valid_profile_returned(self):
        """Happy path: Gemini returns a well-formed profile."""
        client = _mock_client(json.dumps(VALID_PROFILE))
        result = extract_cv_profile(SAMPLE_CV_TEXT, client)

        assert result["seniority_level"] == "senior"
        assert result["total_years_experience"] == 8
        assert len(result["skills"]) == 2
        assert result["skills"][0]["name"] == "Python"
        assert result["industries"] == ["fintech", "e-commerce"]
        assert result["education"]["degree_level"] == "MSc"
        assert result["job_titles"][0] == "Senior Software Engineer"

    def test_prompt_contains_cv_text(self):
        """The CV text must be included in the prompt sent to Gemini."""
        client = _mock_client(json.dumps(VALID_PROFILE))
        extract_cv_profile(SAMPLE_CV_TEXT, client)

        actual_prompt = client.models.generate_content.call_args[1]["contents"]
        assert SAMPLE_CV_TEXT in actual_prompt

    def test_invalid_json_raises_value_error(self):
        """Gemini returning non-JSON must raise ValueError."""
        client = _mock_client("This is not JSON at all")

        with pytest.raises(ValueError, match="invalid JSON"):
            extract_cv_profile(SAMPLE_CV_TEXT, client)

    def test_missing_fields_raises_value_error(self):
        """A profile missing required fields must raise ValueError."""
        incomplete = {"skills": [], "seniority_level": "mid"}
        client = _mock_client(json.dumps(incomplete))

        with pytest.raises(ValueError, match="missing required fields"):
            extract_cv_profile(SAMPLE_CV_TEXT, client)

    def test_api_error_propagates(self):
        """Network / API errors from Gemini must propagate to the caller."""
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError("API down")

        with pytest.raises(RuntimeError, match="API down"):
            extract_cv_profile(SAMPLE_CV_TEXT, client)

    def test_uses_json_response_schema(self):
        """The Gemini call must use structured JSON output with our schema."""
        client = _mock_client(json.dumps(VALID_PROFILE))
        extract_cv_profile(SAMPLE_CV_TEXT, client)

        config = client.models.generate_content.call_args[1]["config"]
        assert config.response_mime_type == "application/json"
        assert config.response_schema == PROFILE_SCHEMA
