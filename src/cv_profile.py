"""One-time structured CV profile extraction using Google Gemini."""

import json

from google import genai
from google.genai import types

GEMINI_MODEL = "gemini-2.5-flash"

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "proficiency": {
                        "type": "string",
                        "enum": ["beginner", "intermediate", "advanced", "expert"],
                    },
                },
                "required": ["name", "proficiency"],
            },
        },
        "seniority_level": {
            "type": "string",
            "enum": ["junior", "mid", "senior", "lead", "principal"],
        },
        "total_years_experience": {"type": "integer"},
        "industries": {
            "type": "array",
            "items": {"type": "string"},
        },
        "education": {
            "type": "object",
            "properties": {
                "degree_level": {"type": "string"},
                "field": {"type": "string"},
            },
            "required": ["degree_level", "field"],
        },
        "job_titles": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "skills",
        "seniority_level",
        "total_years_experience",
        "industries",
        "education",
        "job_titles",
    ],
}

EXTRACTION_PROMPT = """\
You are a senior technical recruiter. Analyse the following CV and extract a \
structured profile.

Instructions:
1. List ALL technical and professional skills mentioned or clearly implied. \
For each skill, assess proficiency as beginner/intermediate/advanced/expert \
based on years used, depth of work described, and context.
2. Determine the candidate's overall seniority level \
(junior/mid/senior/lead/principal) from their most recent roles, scope of \
responsibility, and total experience.
3. Calculate total years of professional experience from the earliest to most \
recent role.
4. Identify industries the candidate has worked in (e.g. fintech, healthcare, \
e-commerce).
5. Extract the highest level of education and its field.
6. List up to 5 most recent job titles, newest first.

CV:
"""


def extract_cv_profile(cv_text: str, client: genai.Client) -> dict:
    """Extract a structured profile from raw CV text via a single Gemini call.

    Args:
        cv_text: Raw text extracted from the candidate's CV.
        client: Initialised Gemini client.

    Returns:
        Parsed profile dict matching PROFILE_SCHEMA.

    Raises:
        ValueError: If Gemini returns invalid JSON or the profile is missing
            required fields.
        Exception: Any API/network error from the Gemini client is propagated.
    """
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=PROFILE_SCHEMA,
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=EXTRACTION_PROMPT + cv_text,
        config=config,
    )

    try:
        profile = json.loads(response.text)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"Gemini returned invalid JSON: {e}") from e

    required_fields = {
        "skills",
        "seniority_level",
        "total_years_experience",
        "industries",
        "education",
        "job_titles",
    }
    missing = required_fields - profile.keys()
    if missing:
        raise ValueError(f"Profile missing required fields: {missing}")

    return profile
