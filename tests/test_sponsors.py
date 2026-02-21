"""Tests for the sponsor register loading and verification logic."""

from unittest.mock import patch

import pandas as pd
import pytest

from src.sponsors import SPONSOR_MATCH_THRESHOLD, load_sponsor_register, verify_sponsors


class TestVerifySponsors:
    """Test fuzzy matching that determines visa sponsorship status.

    False positives = recommending jobs the user can't get a visa for.
    False negatives = hiding legitimate opportunities.
    """

    def test_exact_match_passes(self):
        """Identical names (case-insensitive) must always match at score 100."""
        names = pd.Series(["Deloitte LLP"])
        result = verify_sponsors(names, ["deloitte llp"])
        assert result["verified_sponsor"].iloc[0] == True  # noqa: E712
        assert result["sponsor_match_score"].iloc[0] == 100

    def test_completely_different_name_rejected(self):
        """Unrelated companies must never match — a false positive here
        sends users to companies that can't sponsor their visa."""
        names = pd.Series(["Tesco PLC"])
        result = verify_sponsors(names, ["barclays bank uk plc"])
        assert result["verified_sponsor"].iloc[0] == False  # noqa: E712
        assert result["sponsor_match_score"].iloc[0] == 0

    def test_short_name_does_not_false_match_long_name(self):
        """'Meta' must not match 'metadata solutions ltd' — different companies."""
        names = pd.Series(["Meta"])
        result = verify_sponsors(names, ["metadata solutions ltd"])
        # If it did match, that's a false positive bug
        if result["verified_sponsor"].iloc[0] == True:  # noqa: E712
            assert result["sponsor_match_score"].iloc[0] >= SPONSOR_MATCH_THRESHOLD

    def test_real_world_company_variations(self):
        """Job boards list companies differently than the government register."""
        sponsors = [
            "deloitte llp",
            "google uk limited",
            "amazon uk services ltd.",
            "meta platforms ireland limited",
        ]
        test_cases = [
            ("Deloitte LLP", True),
            ("Deloitte", False),
            ("Amazon", False),
        ]
        for company, should_match in test_cases:
            names = pd.Series([company])
            result = verify_sponsors(names, sponsors)
            matched = result["verified_sponsor"].iloc[0]
            if should_match:
                assert matched == True, f"'{company}' should match but didn't"  # noqa: E712

    def test_nan_mixed_with_valid_preserves_alignment(self):
        """NaN values must not shift results to wrong companies."""
        names = pd.Series([None, "apple", None, "google", None], index=[5, 6, 7, 8, 9])
        result = verify_sponsors(names, ["apple", "google"])

        assert result.loc[5, "verified_sponsor"] == False  # noqa: E712
        assert result.loc[7, "verified_sponsor"] == False  # noqa: E712
        assert result.loc[9, "verified_sponsor"] == False  # noqa: E712
        assert result.loc[6, "verified_sponsor"] == True  # noqa: E712
        assert result.loc[8, "verified_sponsor"] == True  # noqa: E712

    def test_duplicate_companies_matched_once_internally(self):
        """50 jobs from 'Deloitte LLP' should fuzzy-match once, not 50 times."""
        names = pd.Series(["Deloitte LLP"] * 50 + ["Unknown Corp"])
        result = verify_sponsors(names, ["deloitte llp"])

        assert all(result["verified_sponsor"].iloc[:50])
        assert result["verified_sponsor"].iloc[50] == False  # noqa: E712

    def test_empty_string_company_name(self):
        """Job boards sometimes return empty strings for company names."""
        names = pd.Series(["", "  ", "apple"])
        result = verify_sponsors(names, ["apple"])
        assert result["verified_sponsor"].iloc[2] == True  # noqa: E712

    def test_result_dataframe_shape(self):
        """Output must always have exactly 2 columns and same index as input."""
        names = pd.Series(["a", "b", "c"], index=[100, 200, 300])
        result = verify_sponsors(names, ["a"])
        assert list(result.columns) == ["verified_sponsor", "sponsor_match_score"]
        assert list(result.index) == [100, 200, 300]
        assert len(result) == 3


class TestLoadSponsorRegister:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        """Clear lru_cache between tests so mocked responses aren't cached."""
        load_sponsor_register.cache_clear()

    def test_normalises_company_names(self):
        """Gov register has messy data. Without normalisation, fuzzy matching breaks."""
        html = '<a href="https://x.com/Worker.csv">W</a>'
        csv_data = pd.DataFrame(
            {"Organisation Name": ["  DELOITTE LLP  ", "google uk limited", " Apple "]}
        )

        with (
            patch("src.sponsors.requests.get") as mock_get,
            patch("src.sponsors.pd.read_csv", return_value=csv_data),
        ):
            mock_get.return_value.text = html
            result = load_sponsor_register()

        assert result == ("deloitte llp", "google uk limited", "apple")

    def test_drops_nan_rows(self):
        """Blank rows in the CSV must not leak NaN into the sponsor list."""
        html = '<a href="https://x.com/Worker.csv">W</a>'
        csv_data = pd.DataFrame({"Org": ["Apple", None, "", "Google", None]})

        with (
            patch("src.sponsors.requests.get") as mock_get,
            patch("src.sponsors.pd.read_csv", return_value=csv_data),
        ):
            mock_get.return_value.text = html
            result = load_sponsor_register()

        assert None not in result
        assert "apple" in result
        assert "google" in result

    def test_prefers_worker_csv_over_other_csvs(self):
        """Picking the wrong CSV = matching against student sponsors."""
        html = """
        <a href="https://x.com/student_sponsors.csv">Student</a>
        <a href="https://x.com/Worker_and_Temp.csv">Worker</a>
        """
        csv_data = pd.DataFrame({"Org": ["Correct"]})

        with (
            patch("src.sponsors.requests.get") as mock_get,
            patch("src.sponsors.pd.read_csv", return_value=csv_data) as mock_csv,
        ):
            mock_get.return_value.text = html
            load_sponsor_register()

        mock_csv.assert_called_once_with("https://x.com/Worker_and_Temp.csv")

    def test_no_csv_link_returns_empty(self):
        """Graceful failure if gov.uk changes their page structure."""
        html = "<html><body><p>Page redesigned, no CSV links</p></body></html>"
        with patch("src.sponsors.requests.get") as mock_get:
            mock_get.return_value.text = html
            result = load_sponsor_register()
        assert result == ()

    def test_network_timeout_raises(self):
        """Network failure must propagate — caller shows error to user."""
        with patch("src.sponsors.requests.get", side_effect=ConnectionError("timeout")):
            with pytest.raises(ConnectionError):
                load_sponsor_register()

    def test_uses_first_column_regardless_of_name(self):
        """The CSV column name might change. We always read column 0."""
        html = '<a href="https://x.com/Worker.csv">W</a>'
        csv_data = pd.DataFrame({"Completely New Column Name": ["Acme Corp"]})

        with (
            patch("src.sponsors.requests.get") as mock_get,
            patch("src.sponsors.pd.read_csv", return_value=csv_data),
        ):
            mock_get.return_value.text = html
            result = load_sponsor_register()

        assert result == ("acme corp",)
