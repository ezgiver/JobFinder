"""Match-score tier definitions and helpers."""

TIERS = [
    ("Strong Match (80+)", 80, 100),
    ("Good Match (65\u201379)", 65, 79),
    ("Worth a Look (50\u201364)", 50, 64),
]


def assign_tier(score: int) -> str:
    """Return the tier label for a given match score."""
    for label, low, high in TIERS:
        if low <= score <= high:
            return label
    return ""
