"""AI-powered job-to-CV matching using Google Gemini."""

import json
import time

import pandas as pd
from google import genai
from google.genai import types

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_DELAY_SECONDS = 1.5

SYSTEM_PROMPT = """\
You are a highly strategic, perceptive Executive Headhunter. Your job is to \
critically evaluate a candidate's structured profile against a provided Job \
Description (JD) to identify high-quality, realistic matches.

Your grading must be critical and fact-based, but you must also use \
professional intuition and semantic understanding. Candidates and hiring \
managers often use different terminology. You must read between the lines: \
if the profile demonstrates a competency (e.g., "defined acceptance criteria") \
that fulfills a JD requirement (e.g., "agile requirements gathering" or \
"product specification"), count it as a valid match. However, do not \
hallucinate core technical skills or frameworks that are entirely absent.

Grading Rubric for the Match Score (0-100):

90-100: Exceptional fit. Strong evidence of all mandatory and preferred \
skills, plus exact domain experience, whether explicitly stated or clearly \
demonstrated through their achievements.

70-89: Solid fit. Meets core requirements and demonstrates the necessary \
competencies, though they may lack a few 'nice-to-have' skills.

50-69: Borderline. Missing 1-2 core competencies or falls short on the \
required seniority/experience levels.

0-49: Reject. Fundamental mismatch in core domain, seniority, or primary \
technologies.

Instructions:

1. Cross-reference the skills and experience in the structured profile \
against the JD, identifying both explicit keyword matches and implicit \
competency matches.
2. Verify if the seniority level and years of experience align with what \
the JD demands.
3. Output your evaluation strictly as a JSON object.
4. The reasoning must be a single, punchy, and honest sentence explaining \
the exact reason for the score. If the score is below 80, highlight the \
biggest gap. If the score is 80 or above, highlight the strongest matching \
competency."""


def _build_prompt(cv_profile: dict, job_description: str) -> str:
    """Build the scoring prompt using safe string concatenation.

    Avoids str.format() because job descriptions are untrusted external input
    and may contain curly braces (e.g. code snippets in job postings).
    """
    profile_json = json.dumps(cv_profile, indent=2)
    return (
        SYSTEM_PROMPT
        + "\n\nCandidate Profile:\n"
        + profile_json
        + "\n\nJob Description:\n"
        + job_description
    )

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "match_score": {"type": "integer"},
        "reasoning": {"type": "string"},
    },
    "required": ["match_score", "reasoning"],
}


def score_jobs(
    cv_profile: dict,
    df: pd.DataFrame,
    client: genai.Client,
    progress_callback=None,
) -> pd.DataFrame:
    """Score each job in the DataFrame against the candidate profile using Gemini.

    Args:
        cv_profile: Structured profile dict extracted from the candidate's CV.
        df: DataFrame with a 'description' column.
        client: Initialised Gemini client.
        progress_callback: Optional callable(current, total) for progress updates.

    Returns:
        Copy of df with 'match_score' and 'reasoning' columns added.
    """
    if "description" not in df.columns:
        df = df.copy()
        df["match_score"] = 0
        df["reasoning"] = "No description column in data."
        return df

    scores = []
    reasonings = []
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=SCORE_SCHEMA,
    )

    total = len(df)
    for idx, (_, row) in enumerate(df.iterrows()):
        if progress_callback:
            progress_callback(idx + 1, total)

        description = row.get("description") or ""
        if not description.strip():
            scores.append(0)
            reasonings.append("No job description available.")
            continue

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=_build_prompt(cv_profile, description),
                config=config,
            )
            result = json.loads(response.text)
            scores.append(result["match_score"])
            reasonings.append(result["reasoning"])
        except Exception as e:
            scores.append(0)
            reasonings.append(f"Scoring failed: {e}")

        # Rate-limit delay between Gemini calls
        if idx < total - 1:
            time.sleep(GEMINI_DELAY_SECONDS)

    df = df.copy()
    df["match_score"] = scores
    df["reasoning"] = reasonings
    return df
