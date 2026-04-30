"""Match tier assignment for scored job results."""


def assign_tier(score: float) -> str:
    """Return a human-readable tier label for a given match score (0–100).

    Parameters
    ----------
    score:
        Numeric match score between 0 and 100.

    Returns
    -------
    str
        One of: "Strong Match", "Good Match", "Weak Match", "Poor Match".
    """
    if score >= 80:
        return "Strong Match"
    if score >= 60:
        return "Good Match"
    if score >= 40:
        return "Weak Match"
    return "Poor Match"
