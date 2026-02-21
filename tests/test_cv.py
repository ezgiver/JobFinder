"""Tests for CV text extraction."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.cv_parser import extract_cv_text


class TestExtractCvText:
    def test_pdf_extracts_all_pages(self):
        """A 3-page CV must return text from ALL pages.
        Bug here = missing work experience from page 2-3."""
        pages = []
        for text in ["Name: John", "Experience: 5 years Python", "Education: MSc"]:
            p = MagicMock()
            p.get_text.return_value = text
            pages.append(p)

        mock_doc = MagicMock()
        mock_doc.__enter__ = MagicMock(return_value=mock_doc)
        mock_doc.__exit__ = MagicMock(return_value=False)
        mock_doc.__iter__ = MagicMock(return_value=iter(pages))

        uploaded = SimpleNamespace(name="cv.pdf", read=lambda: b"bytes")
        with patch("src.cv_parser.fitz.open", return_value=mock_doc):
            result = extract_cv_text(uploaded)

        assert "Name: John" in result
        assert "Experience: 5 years Python" in result
        assert "Education: MSc" in result

    def test_docx_preserves_paragraph_separation(self):
        """Paragraphs must be newline-separated, otherwise skills run into
        job titles and confuse the AI scorer."""
        paras = [MagicMock(text=t) for t in ["Skills: Python", "Company: Google"]]
        mock_doc = MagicMock(paragraphs=paras)

        uploaded = SimpleNamespace(name="cv.docx", read=lambda: b"bytes")
        with patch("src.cv_parser.Document", return_value=mock_doc):
            result = extract_cv_text(uploaded)

        assert result == "Skills: Python\nCompany: Google"
        assert "Python\nCompany" in result

    def test_unsupported_format_returns_empty(self):
        """Uploading a .txt or .odt should not crash."""
        for ext in [".txt", ".odt", ".rtf", ".pages"]:
            uploaded = SimpleNamespace(name=f"cv{ext}", read=lambda: b"data")
            result = extract_cv_text(uploaded)
            assert result == "", f"Expected empty string for {ext}"

    def test_pdf_library_crash_propagates(self):
        """A corrupt PDF error must bubble up — not silently return empty,
        which would cause the AI to score against nothing."""
        uploaded = SimpleNamespace(name="corrupt.pdf", read=lambda: b"not a pdf")
        with patch("src.cv_parser.fitz.open", side_effect=RuntimeError("corrupt PDF")):
            with pytest.raises(RuntimeError, match="corrupt PDF"):
                extract_cv_text(uploaded)
