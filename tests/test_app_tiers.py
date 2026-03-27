"""Tests for tiered match display logic."""

import pytest

from src.tiers import TIERS, assign_tier


class TestAssignTier:
    """Verify tier labels are assigned correctly at all boundaries."""

    @pytest.mark.parametrize(
        "score, expected",
        [
            (100, "Strong Match (80+)"),
            (90, "Strong Match (80+)"),
            (80, "Strong Match (80+)"),
            (79, "Good Match (65–79)"),
            (72, "Good Match (65–79)"),
            (65, "Good Match (65–79)"),
            (64, "Worth a Look (50–64)"),
            (55, "Worth a Look (50–64)"),
            (50, "Worth a Look (50–64)"),
            (49, ""),
            (0, ""),
        ],
    )
    def test_tier_assignment(self, score, expected):
        assert assign_tier(score) == expected

    def test_tiers_cover_full_range_without_gaps(self):
        """Tiers should cover 50-100 contiguously."""
        covered = set()
        for _, low, high in TIERS:
            covered.update(range(low, high + 1))
        assert covered == set(range(50, 101))
