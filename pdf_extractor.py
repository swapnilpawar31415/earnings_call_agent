# pdf_extractor.py
# Extracts plain text from downloaded transcript PDFs.

import logging
import pdfplumber
from pathlib import Path
from config import MAX_TRANSCRIPT_CHARS

logger = logging.getLogger(__name__)


def extract_text(filepath: str) -> str | None:
    """
    Extract text from a PDF file.
    Returns cleaned text string, or None if extraction fails.
    Truncates to MAX_TRANSCRIPT_CHARS to stay within LLM context limits.
    """
    path = Path(filepath)
    if not path.exists():
        logger.error(f"PDF not found: {filepath}")
        return None

    try:
        with pdfplumber.open(filepath) as pdf:
            pages_text = []
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    pages_text.append(text)

        if not pages_text:
            logger.warning(f"No text extracted from {filepath} — may be a scanned PDF.")
            return None

        full_text = "\n\n".join(pages_text)

        # Basic cleanup
        full_text = "\n".join(
            line for line in full_text.splitlines()
            if line.strip()
        )

        if len(full_text) > MAX_TRANSCRIPT_CHARS:
            logger.info(
                f"Transcript truncated from {len(full_text)} "
                f"to {MAX_TRANSCRIPT_CHARS} chars."
            )
            full_text = full_text[:MAX_TRANSCRIPT_CHARS]

        logger.info(f"Extracted {len(full_text)} chars from {path.name}")
        return full_text

    except Exception as e:
        logger.error(f"PDF extraction failed for {filepath}: {e}")
        return None
