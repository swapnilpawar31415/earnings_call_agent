# bse_fetcher.py
# Polls BSE corporate announcements API for transcript filings
# and downloads the PDFs.
#
# BSE's API (api.bseindia.com) requires requests to originate from a real
# browser context — plain requests/cloudscraper return an empty {}.
# We use Playwright (headless Chromium) to warm up the session and call
# the API via in-page fetch(). PDF downloads use plain requests, which
# works fine against BSE's file CDN.

import time
import logging
import requests
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

PDF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/pdf,*/*",
}

from config import (
    STORAGE_BASE_DIR,
    LOOKBACK_DAYS,
    TRANSCRIPT_KEYWORDS,
)
from bse500_codes import BSE500_CODES

logger = logging.getLogger(__name__)

# ─── BSE constants ────────────────────────────────────────────────────────────
BSE_ANN_PAGE  = "https://www.bseindia.com/corporates/ann.html"
BSE_ANN_API   = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
BSE_PDF_BASE  = "https://www.bseindia.com/xml-data/corpfiling/AttachLive"

BSE_PAGE_SIZE = 50   # rows per API page (observed in production)

BSE500_SET = set(BSE500_CODES)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _default_date_range() -> tuple[str, str]:
    """Return (from_date_str, to_date_str) covering LOOKBACK_DAYS back to today."""
    today     = datetime.now()
    from_date = today - timedelta(days=LOOKBACK_DAYS)
    return from_date.strftime("%Y%m%d"), today.strftime("%Y%m%d")


def _is_transcript(announcement: dict) -> bool:
    """Return True if this announcement looks like a concall transcript.

    'transcript' and 'concall' are strong signals and match on their own.
    Broader terms ('earnings call', 'investor meet', etc.) are weak signals
    that only match when the filing doesn't look like a notice/intimation
    (e.g. "Company to participate in Kotak conference").
    """
    fields = [
        announcement.get("NEWSSUB")      or "",
        announcement.get("HEADLINE")     or "",
        announcement.get("CATEGORYNAME") or "",
        announcement.get("SUBCATNAME")   or "",
    ]
    combined = " ".join(fields).lower()

    # Strong keywords — unambiguous regardless of context
    if any(kw in combined for kw in ["transcript", "concall"]):
        return True

    # Weak keywords — skip if the filing is clearly a notice/schedule
    NOTICE_WORDS = ["intimat", "schedul", "notice", "participat", "attend", "inform"]
    if any(nw in combined for nw in NOTICE_WORDS):
        return False

    return any(kw in combined for kw in ["earnings call", "investor meet", "analyst meet", "conference call"])


def _is_bse500(announcement: dict) -> bool:
    """Return True if the company is in BSE500."""
    try:
        scrip = int(announcement.get("SCRIP_CD", 0))
        return scrip in BSE500_SET
    except (ValueError, TypeError):
        return False


def _pdf_url(announcement: dict) -> str | None:
    """Construct the PDF download URL from the announcement record."""
    attachment = announcement.get("ATTACHMENTNAME") or ""
    if not attachment:
        return None
    if attachment.startswith("http"):
        return attachment
    return f"{BSE_PDF_BASE}/{attachment.lstrip('/')}"


def _api_url(pageno: int, from_date: str, to_date: str) -> str:
    params = (
        f"pageno={pageno}&strCat=-1&strPrevDate={from_date}"
        f"&strScrip=&strSearch=P&strToDate={to_date}"
        f"&strType=C&subcategory=-1"
    )
    return f"{BSE_ANN_API}?{params}"


# ─── Core fetch ───────────────────────────────────────────────────────────────

def _fetch_single_day(page, date_str: str) -> list[dict]:
    """Fetch all announcements for a single YYYYMMDD date using an existing browser page."""
    day_anns: list[dict] = []
    pageno = 1
    while True:
        url = _api_url(pageno, date_str, date_str)
        try:
            result = page.evaluate(f"""async () => {{
                const r = await fetch("{url}", {{
                    headers: {{
                        "Accept": "application/json, text/plain, */*",
                        "Accept-Language": "en-US,en;q=0.9",
                    }}
                }});
                return await r.json();
            }}""")
        except Exception as e:
            logger.error(f"API fetch failed for {date_str} page {pageno}: {e}")
            break

        rows = result.get("Table", []) if isinstance(result, dict) else []
        if not rows:
            if pageno == 1:
                logger.debug(f"{date_str}: 0 announcements from API")
            break

        day_anns.extend(rows)
        if len(rows) < BSE_PAGE_SIZE:
            break
        pageno += 1

    return day_anns


def fetch_announcement_list(from_date: str = None, to_date: str = None) -> list[dict]:
    """
    Return all BSE500 transcript filings in the date range.

    from_date / to_date: YYYYMMDD strings (inclusive).
    If omitted, falls back to the default LOOKBACK_DAYS window.

    BSE's API only returns data reliably for single-day queries, so we
    iterate day-by-day across the requested range.
    """
    if not from_date or not to_date:
        from_date, to_date = _default_date_range()
    logger.info(f"Fetching BSE announcements from {from_date} to {to_date}")

    # Build list of calendar dates in range
    start = datetime.strptime(from_date, "%Y%m%d")
    end   = datetime.strptime(to_date,   "%Y%m%d")
    dates = []
    cur = start
    while cur <= end:
        dates.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)

    all_anns: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()

        # Warm-up: establish browser session on the announcements page
        page.goto(BSE_ANN_PAGE, wait_until="networkidle", timeout=40_000)

        for date_str in dates:
            day_rows = _fetch_single_day(page, date_str)
            logger.info(f"{date_str}: {len(day_rows)} announcements")
            all_anns.extend(day_rows)

        browser.close()

    logger.info(f"Total announcements returned: {len(all_anns)}")
    filtered = [a for a in all_anns if _is_bse500(a) and _is_transcript(a)]
    logger.info(f"BSE500 transcript filings found: {len(filtered)}")
    return filtered


# ─── PDF download ─────────────────────────────────────────────────────────────

def _ann_folder(ann: dict) -> str:
    """Return YYYYMMDD from the announcement's publication date."""
    raw = ann.get("NEWS_DT") or ann.get("NEWSDATE") or ""
    if raw:
        # NEWS_DT looks like "2026-02-17T14:58:41.317"
        return raw[:10].replace("-", "")
    return datetime.now().strftime("%Y%m%d")


def download_transcripts(announcements: list[dict]) -> list[dict]:
    """
    Download PDFs for the given announcements.

    Each PDF is saved under transcripts/{YYYYMMDD}/ matching its BSE
    publication date, so re-running any date range reuses existing per-day
    folders and skips already-downloaded files automatically.
    """
    base = Path(STORAGE_BASE_DIR)

    results = []
    skipped = 0

    # Warm up a Playwright browser to collect BSE session cookies,
    # then pass those cookies to the requests session for PDF downloads.
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BSE_ANN_PAGE, wait_until="networkidle", timeout=40_000)
        bse_cookies = {
            c["name"]: c["value"]
            for c in page.context.cookies()
            if "bseindia.com" in c.get("domain", "")
        }
        browser.close()
    logger.debug(f"Captured {len(bse_cookies)} BSE session cookies for download")

    session = requests.Session()
    session.headers.update(PDF_HEADERS)
    session.cookies.update(bse_cookies)

    for ann in announcements:
        scrip_cd = ann.get("SCRIP_CD", "UNKNOWN")
        company  = ann.get("SCRIP_NAME") or ann.get("SLONGNAME") or "Unknown"
        ann_no   = ann.get("ANNOUNCEMENTNO") or ann.get("NEWSID") or "0"
        headline = ann.get("HEADLINE") or ann.get("NEWSSUB") or ""
        ann_date = ann.get("NEWS_DT") or ann.get("NEWSDATE") or ""

        save_dir = base / _ann_folder(ann)
        save_dir.mkdir(parents=True, exist_ok=True)

        pdf_url = _pdf_url(ann)
        if not pdf_url:
            logger.warning(f"No PDF URL for {company} ({scrip_cd}), skipping.")
            continue

        safe_name = "".join(c if c.isalnum() else "_" for c in company)
        filename  = f"{safe_name}_{scrip_cd}_{ann_no}.pdf"
        filepath  = save_dir / filename

        if filepath.exists():
            logger.debug(f"Already downloaded: {filepath}")
            skipped += 1
        else:
            try:
                logger.info(f"Downloading: {company} → {pdf_url}")
                r = session.get(pdf_url, timeout=30)
                if not r.ok:
                    logger.error(
                        f"HTTP {r.status_code} for {company}: {pdf_url}"
                    )
                    continue
                filepath.write_bytes(r.content)
                logger.info(
                    f"Saved: {filepath} ({len(r.content)//1024} KB)"
                )
                time.sleep(0.5)   # polite crawl delay
            except Exception as e:
                logger.error(f"Download failed for {company}: {e}")
                continue

        results.append({
            "company":  company,
            "scrip_cd": scrip_cd,
            "ann_date": ann_date,
            "headline": headline,
            "pdf_url":  pdf_url,
            "filepath": str(filepath),
        })

    logger.info(
        f"Download complete — {len(results)} saved, {skipped} already existed, "
        f"{len(announcements) - len(results) - skipped} failed."
    )
    return results
