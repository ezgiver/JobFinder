# Job Finder

AI-powered job search tool for UK visa-sponsored roles. Scrapes job listings, verifies employers against the official UK government sponsor register, and scores each job against your CV using Google Gemini.

## How It Works

1. **You enter a target role** (e.g. "Software Engineer") and upload your CV
2. **Scrapes Indeed + LinkedIn** for matching jobs in London
3. **Cross-references** each employer against the [UK Home Office sponsor register](https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers) using fuzzy matching — only keeps jobs from verified visa sponsors
4. **Scores each job against your CV** using Gemini 2.5 Flash, acting as an executive headhunter that evaluates skill matches, experience level, and domain fit
5. **Returns a ranked list** of jobs scoring 70+ with one-click CSV export

## Architecture

```
app.py          Streamlit UI — forms, progress bars, display
sponsors.py     UK sponsor register loading + fuzzy company matching
cv.py           PDF/DOCX text extraction
scoring.py      Gemini AI job-to-CV scoring pipeline
```

**Key design decisions:**
- **Fuzzy matching** (rapidfuzz, threshold 85) handles company name variations between job boards and the government register (e.g. "Deloitte" vs "Deloitte LLP")
- **Deduplication** — unique company names are matched once regardless of how many jobs reference them
- **Rate limiting** — 1.5s delay between Gemini API calls to avoid throttling
- **Structured output** — Gemini responses use `response_schema` to enforce JSON with `match_score` (0-100) and `reasoning`

## Setup

**Prerequisites:** Python 3.13+, [uv](https://docs.astral.sh/uv/)

```bash
# Clone and install
git clone <repo-url>
cd job-finder
uv sync

# Set your Gemini API key
export GEMINI_API_KEY="your-key-here"

# Run
uv run streamlit run app.py
```

Get a Gemini API key at [aistudio.google.com](https://aistudio.google.com/apikey).

## Testing

```bash
uv run pytest tests/ -v
```

27 tests covering:
- **Sponsor matching** — threshold boundaries, real-world company name variations, NaN handling, index alignment
- **CV extraction** — multi-page PDFs, paragraph separation in DOCX, corrupt file handling
- **AI scoring** — prompt correctness, score-to-row alignment, invalid/unexpected Gemini responses, rate limiting, partial failure recovery

## Tech Stack

- **Streamlit** — Web UI
- **python-jobspy** — Job board scraping (Indeed, LinkedIn)
- **Google Gemini 2.5 Flash** — CV-to-job matching
- **rapidfuzz** — Fuzzy string matching for sponsor verification
- **PyMuPDF + python-docx** — CV text extraction
- **pandas** — Data pipeline
