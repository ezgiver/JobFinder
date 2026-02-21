"""CV text extraction from PDF and DOCX files."""

import io
from typing import Protocol

import fitz
from docx import Document


class UploadedFile(Protocol):
    """Minimal interface for an uploaded file (matches Streamlit's UploadedFile)."""

    name: str

    def read(self) -> bytes: ...


def extract_cv_text(uploaded_file: UploadedFile) -> str:
    """Extract plain text from an uploaded PDF or DOCX file."""
    name = uploaded_file.name.lower()
    data = uploaded_file.read()

    if name.endswith(".pdf"):
        pages = []
        with fitz.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                pages.append(page.get_text())
        return "".join(pages)

    if name.endswith(".docx"):
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)

    return ""
