# bse_fetcher.py
# Polls BSE corporate announcements API for transcript filings
# and downloads the PDFs.

import os
import time
import logging
import requests
from datetime import datetime, timedelta
from pathlib import Path

from config import (
    STORAGE_BASE_DIR,
    LOOKBACK_DAYS,
    TRANSCRIPT_KEYWORDS,
)
from bse500_codes import BSE500_CODES

logger = logging.getLogger(__name__)

# ─── BSE API constants ────────────────────────────────────────────────────────
BSE_ANN_API   = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
BSE_PDF_BASE  = "https://www.bseindia.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json, text/plain, */*",
}

BSE500_SET = set(BSE500_CODES)


def _date_range():
    """Return (from_date_str, to_date_str) covering LOOKBACK_DAYS back to today."""
    today     = datetime.now()
    from_date = today - timedelta(days=LOOKBACK_DAYS)
    return from_date.strftime("%Y%m%d"), today.strftime("%Y%m%d")


def _is_transcript(announcement: dict) -> bool:
    """Return True if this announcement looks like a concall transcript."""
    fields = [
        announcement.get("NEWSSUB", ""),
        announcement.get("HEADLINE", ""),
        announcement.get("CATEGORYNAME", ""),
        announcement.get("SUBCATNAME", ""),
    ]
    combined = " ".join(fields).lower()
    return any(kw in combined for kw in TRANSCRIPT_KEYWORDS)


def _is_bse500(announcement: dict) -> bool:
    """Return True if the company is in BSE500."""
    try:
        scrip = int(announcement.get("SCRIP_CD", 0))
        return scrip in BSE500_SET
    except (ValueError, TypeError):
        return False


def fetch_announcement_list() -> list[dict]:
    """
    Hit the BSE announcements API and return all announcements
    from the lookback window that are (a) BSE500 and (b) transcripts.
    """
    from_date, to_date = _date_range()
    logger.info(f"Fetching BSE announcements from {from_date} to {to_date}")

    params = {
        "pageno":       1,
        "strCat":       "-1",       # all categories
        "strPrevDate":  from_date,
        "strScrip":     "",
        "strSearch":    "P",
        "strToDate":    to_date,
        "strType":      "C",        # company announcements
        "subcategory":  "-1",
    }

    try:
        resp = requests.get(
            BSE_ANN_API, params=params, headers=HEADERS, timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"BSE API call failed: {e}")
        return []

    all_anns = data.get("Table", [])
    logger.info(f"Total announcements returned: {len(all_anns)}")

    filtered = [a for a in all_anns if _is_bse500(a) and _is_transcript(a)]
    logger.info(f"BSE500 transcript filings found: {len(filtered)}")
    return filtered


def _pdf_url(announcement: dict) -> str | None:
    """Construct the PDF download URL from the announcement record."""
    # BSE API returns ATTACHMENTNAME with relative path
    attachment = announcement.get("ATTACHMENTNAME", "")
    if not attachment:
        return None
    if attachment.startswith("http"):
        return attachment
    return f"{BSE_PDF_BASE}/{attachment.lstrip('/')}"


def download_transcripts(announcements: list[dict]) -> list[dict]:
    """
    Download PDFs for the given announcements.
    Returns a list of dicts with metadata + local file path.
    """
    base = Path(STORAGE_BASE_DIR)
    date_str = datetime.now().strftime("%Y-%m-%d")
    save_dir = base / date_str
    save_dir.mkdir(parents=True, exist_ok=True)

    results = []

    for ann in announcements:
        scrip_cd   = ann.get("SCRIP_CD", "UNKNOWN")
        company    = ann.get("SCRIP_NAME", ann.get("SLONGNAME", "Unknown"))
        ann_no     = ann.get("ANNOUNCEMENTNO", ann.get("NEWSID", "0"))
        headline   = ann.get("HEADLINE", ann.get("NEWSSUB", ""))
        ann_date   = ann.get("NEWS_DT", ann.get("NEWSDATE", date_str))

        pdf_url = _pdf_url(ann)
        if not pdf_url:
            logger.warning(f"No PDF URL for {company} ({scrip_cd}), skipping.")
            continue

        # Sanitise filename
        safe_name = "".join(c if c.isalnum() else "_" for c in company)
        filename  = f"{safe_name}_{scrip_cd}_{ann_no}.pdf"
        filepath  = save_dir / filename

        if filepath.exists():
            logger.info(f"Already downloaded: {filepath}")
        else:
            try:
                logger.info(f"Downloading: {company} → {pdf_url}")
                r = requests.get(pdf_url, headers=HEADERS, timeout=30)
                r.raise_for_status()
                filepath.write_bytes(r.content)
                logger.info(f"Saved: {filepath} ({len(r.content)//1024} KB)")
                time.sleep(0.5)   # polite crawl delay
            except Exception as e:
                logger.error(f"Download failed for {company}: {e}")
                continue

        results.append({
            "company":   company,
            "scrip_cd":  scrip_cd,
            "ann_date":  ann_date,
            "headline":  headline,
            "pdf_url":   pdf_url,
            "filepath":  str(filepath),
        })

    return results
