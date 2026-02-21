"""UK visa sponsor register loading and verification."""

from functools import lru_cache

import pandas as pd
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process

SPONSOR_PAGE_URL = (
    "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
)
SPONSOR_MATCH_THRESHOLD = 85


@lru_cache(maxsize=1)
def load_sponsor_register() -> tuple[str, ...]:
    """Download the official UK sponsor register and return normalised company names.

    Returns a tuple (hashable for lru_cache) of lowercased, stripped company names.
    Cached in-process so repeated calls don't re-download the 11MB CSV.
    """
    page = requests.get(SPONSOR_PAGE_URL, timeout=30)
    soup = BeautifulSoup(page.text, "html.parser")
    csv_links = soup.select('a[href$=".csv"]')
    # Prefer the link whose text or href mentions "Worker"
    csv_url = None
    for link in csv_links:
        href = link.get("href", "")
        text = link.get_text(strip=True).lower()
        if "worker" in href.lower() or "worker" in text:
            csv_url = href
            break
    if not csv_url and csv_links:
        csv_url = csv_links[0]["href"]
    if not csv_url:
        return ()
    try:
        df = pd.read_csv(csv_url)
    except (pd.errors.ParserError, pd.errors.EmptyDataError):
        return ()
    col = df.columns[0]
    return tuple(df[col].dropna().str.strip().str.lower())


def verify_sponsors(
    company_names: pd.Series, sponsors: list[str] | tuple[str, ...]
) -> pd.DataFrame:
    """Batch fuzzy-match company names against the sponsor register."""
    unique_names = company_names.dropna().unique().tolist()
    normalised = [str(n).strip().lower() for n in unique_names]

    match_map: dict[str, tuple[bool, int]] = {}
    for name, norm in zip(unique_names, normalised):
        result = process.extractOne(
            norm,
            sponsors,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=SPONSOR_MATCH_THRESHOLD,
        )
        if result:
            match_map[name] = (True, int(result[1]))
        else:
            match_map[name] = (False, 0)

    verified = company_names.map(
        lambda c: match_map.get(c, (False, 0)) if pd.notna(c) else (False, 0)
    )
    return pd.DataFrame(
        {
            "verified_sponsor": verified.apply(lambda r: r[0]),
            "sponsor_match_score": verified.apply(lambda r: r[1]),
        },
        index=company_names.index,
    )
