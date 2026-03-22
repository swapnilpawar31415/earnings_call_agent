# nse_fetcher.py
# Polls NSE corporate announcements API for transcript filings
# and downloads the PDFs.
#
# NSE's API returns all equity announcements for a date range in a single call
# (no per-symbol queries needed). The BennyThadikaran/NseIndiaApi library
# (pip install nse) handles NSE's cookie/session management.
# PDF URLs on nsearchives.nseindia.com are stable and don't expire.

import time
import logging
import requests
from datetime import datetime, timedelta
from pathlib import Path

from nse import NSE as NseClient

from config import STORAGE_BASE_DIR, LOOKBACK_DAYS

logger = logging.getLogger(__name__)

NSE_WORKDIR = "/tmp/nse_session"

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _is_transcript(ann: dict) -> bool:
    """Return True if this NSE announcement is a concall transcript.

    'transcript' in attchmntText is a strong, unambiguous signal.
    The desc field 'Analysts/Institutional Investor Meet/Con. Call Updates'
    is used for both schedules and actual transcripts, so we rely on text.
    """
    combined = " ".join([
        ann.get("desc")         or "",
        ann.get("attchmntText") or "",
    ]).lower()

    # Strong signals — unambiguous
    if "transcript" in combined:
        return True
    if "concall" in combined:
        NOTICE_WORDS = ["intimat", "schedul", "notice", "invite", "participat"]
        if not any(nw in combined for nw in NOTICE_WORDS):
            return True

    # Weak signals — only if not a schedule/notice
    NOTICE_WORDS = ["intimat", "schedul", "notice", "participat", "attend", "inform about schedule", "invite"]
    if any(nw in combined for nw in NOTICE_WORDS):
        return False

    return any(kw in combined for kw in [
        "earnings call", "investor meet", "analyst meet", "conference call"
    ])


def _get_nifty500_symbols(nse_client: NseClient) -> set[str]:
    """Fetch current Nifty500 constituents from NSE."""
    try:
        result = nse_client.listEquityStocksByIndex("NIFTY 500")
        data = result.get("data", [])
        symbols = {d["symbol"] for d in data if isinstance(d, dict) and d.get("symbol")}
        symbols.discard("NIFTY 500")  # remove the index entry itself
        logger.info(f"[NSE] Loaded {len(symbols)} Nifty500 symbols")
        return symbols
    except Exception as e:
        logger.warning(f"[NSE] Failed to fetch Nifty500 symbols: {e} — index filter disabled")
        return set()


def _ann_folder(ann: dict) -> str:
    """Return YYYYMMDD from the announcement date."""
    # an_dt: "24-Feb-2025 23:55:31"  |  sort_date: "2025-02-24 23:55:31"
    dt_str = ann.get("sort_date") or ann.get("an_dt") or ""
    try:
        if dt_str and "-" in dt_str and len(dt_str) > 10 and dt_str[2] == "-":
            dt = datetime.strptime(dt_str.split()[0], "%d-%b-%Y")
        else:
            dt = datetime.strptime(dt_str[:10], "%Y-%m-%d")
        return dt.strftime("%Y%m%d")
    except Exception:
        return datetime.now().strftime("%Y%m%d")


# ─── Core fetch ───────────────────────────────────────────────────────────────

def fetch_announcement_list(from_date: str = None, to_date: str = None) -> list[dict]:
    """
    Return all Nifty500 transcript filings in the date range.

    from_date / to_date: YYYYMMDD strings (inclusive).
    If omitted, falls back to the LOOKBACK_DAYS default window.

    A single NSE API call returns all equity announcements for the date range —
    no per-symbol iteration needed.
    """
    if not from_date or not to_date:
        today = datetime.now()
        to_dt = today
        from_dt = today - timedelta(days=LOOKBACK_DAYS)
    else:
        from_dt = datetime.strptime(from_date, "%Y%m%d")
        to_dt   = datetime.strptime(to_date,   "%Y%m%d")

    logger.info(f"[NSE] Fetching announcements from {from_date} to {to_date}")

    nse = NseClient(NSE_WORKDIR)
    try:
        nifty500 = _get_nifty500_symbols(nse)
        all_anns = nse.announcements(from_date=from_dt.date(), to_date=to_dt.date())
    finally:
        nse.exit()

    logger.info(f"[NSE] Total announcements returned: {len(all_anns)}")

    filtered = []
    for ann in all_anns:
        symbol = ann.get("symbol", "").upper()
        if nifty500 and symbol not in nifty500:
            continue
        if not _is_transcript(ann):
            continue
        if not ann.get("attchmntFile"):
            logger.debug(f"[NSE] No attachment for {symbol}, skipping.")
            continue
        filtered.append(ann)

    logger.info(f"[NSE] Nifty500 transcript filings found: {len(filtered)}")
    return filtered


# ─── PDF download ─────────────────────────────────────────────────────────────

def download_transcripts(announcements: list[dict]) -> list[dict]:
    """
    Download PDFs for the given NSE announcements.

    NSE archive URLs (nsearchives.nseindia.com) are stable and don't expire.
    No session token is required for the download itself.
    """
    base = Path(STORAGE_BASE_DIR)

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.nseindia.com/",
        "Accept":  "application/pdf,*/*",
    })

    results = []
    skipped = 0

    for ann in announcements:
        symbol   = ann.get("symbol", "UNKNOWN").upper()
        company  = ann.get("sm_name") or symbol
        seq_id   = ann.get("seq_id") or ann.get("dt") or "0"
        headline = ann.get("attchmntText") or ann.get("desc") or ""
        ann_date = ann.get("an_dt") or ann.get("sort_date") or ""
        pdf_url  = ann.get("attchmntFile", "")

        if not pdf_url:
            logger.warning(f"[NSE] No PDF URL for {company}, skipping.")
            continue

        save_dir = base / _ann_folder(ann)
        save_dir.mkdir(parents=True, exist_ok=True)

        safe_name = "".join(c if c.isalnum() else "_" for c in company)
        filename  = f"{safe_name}_{symbol}_{seq_id}.pdf"
        filepath  = save_dir / filename

        if filepath.exists():
            logger.debug(f"[NSE] Already downloaded: {filepath}")
            skipped += 1
        else:
            try:
                logger.info(f"[NSE] Downloading: {company} → {pdf_url}")
                r = session.get(pdf_url, timeout=30)
                if not r.ok:
                    logger.error(f"[NSE] HTTP {r.status_code} for {company}: {pdf_url}")
                    continue
                filepath.write_bytes(r.content)
                logger.info(f"[NSE] Saved: {filepath} ({len(r.content)//1024} KB)")
                time.sleep(0.3)
            except Exception as e:
                logger.error(f"[NSE] Download failed for {company}: {e}")
                continue

        results.append({
            "company":  company,
            "scrip_cd": f"NSE:{symbol}",   # namespaced to avoid collision with BSE numeric codes
            "ann_date": ann_date,
            "headline": headline,
            "pdf_url":  pdf_url,
            "filepath": str(filepath),
        })

    newly_saved = len(results) - skipped
    logger.info(
        f"[NSE] Download complete — {newly_saved} newly saved, {skipped} already existed, "
        f"{len(announcements) - len(results)} failed."
    )
    return results
