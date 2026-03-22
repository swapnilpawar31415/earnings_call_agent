#!/usr/bin/env python3
# main.py
# Earnings Call Transcript Agent — Main Orchestrator
#
# Usage:
#   python main.py                                # Normal run (NSE, yesterday's transcripts)
#   python main.py --dry-run                      # Fetch & download only, no email
#   python main.py --from 20250201 --to 20250228  # Date range (results season backfill)
#   python main.py --from 20250305                # Single date (--to defaults to today)
#   python main.py --source bse                   # Use BSE instead of NSE (default: nse)
#
# Schedule with cron (runs at 7am daily):
#   0 7 * * * cd /path/to/earnings_agent && python main.py >> logs/cron.log 2>&1

import os
import sys
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path

# ─── Logging setup ────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
log_file = Path("logs") / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file),
    ],
)
logger = logging.getLogger("main")


def save_analysis(analyses: list[dict], label: str = None):
    """Persist analyses to JSON alongside the transcripts."""
    from config import STORAGE_BASE_DIR
    date_str  = label or datetime.now().strftime("%Y-%m-%d")
    save_path = Path(STORAGE_BASE_DIR) / date_str / "analyses.json"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w") as f:
        json.dump(analyses, f, indent=2, ensure_ascii=False)
    logger.info(f"Analyses saved → {save_path}")


def run(dry_run: bool = False, from_date: str = None, to_date: str = None, limit: int = None, source: str = "nse"):
    """Main pipeline."""
    logger.info("=" * 60)
    logger.info("Earnings Transcript Agent — starting run")
    if from_date or to_date:
        logger.info(f"Date range: {from_date or 'default'} → {to_date or 'today'}")
    logger.info(f"Source: {source.upper()}")
    logger.info("=" * 60)

    # ── Step 1: Fetch announcement list ──────────────────────────
    if source == "bse":
        import bse_fetcher as fetcher
    else:
        import nse_fetcher as fetcher

    announcements = fetcher.fetch_announcement_list(from_date=from_date, to_date=to_date)
    logger.info(f"Filings found — {source.upper()}: {len(announcements)}")

    if not announcements:
        logger.info("No transcript filings found for the period.")
        if not dry_run:
            from emailer import send_digest
            send_digest(analyses=[], failed=[])
        logger.info("Done — nothing to process.")
        return

    if limit:
        announcements = announcements[:limit]
        logger.info(f"Limiting to first {limit} transcript(s) (--limit flag).")

    # ── Step 2: Download PDFs ─────────────────────────────────────
    downloaded = fetcher.download_transcripts(announcements)
    logger.info(f"Successfully downloaded: {len(downloaded)} PDF(s).")

    if not downloaded:
        logger.warning("All downloads failed.")
        if not dry_run:
            from emailer import send_digest
            send_digest(analyses=[], failed=[
                a.get("sm_name") or a.get("SCRIP_NAME") or a.get("SLONGNAME") or a.get("SCRIP_CD", "Unknown")
                for a in announcements
            ])
        return

    if dry_run:
        logger.info("Dry-run mode — stopping before analysis.")
        return

    # Deduplicate by scrip_cd — exchange sometimes files the same transcript twice
    seen_scrips: set[str] = set()
    deduped = []
    for item in downloaded:
        sc = item["scrip_cd"]
        if sc not in seen_scrips:
            seen_scrips.add(sc)
            deduped.append(item)
        else:
            logger.info(f"Duplicate filing for {item['company']} ({sc}) — skipping")
    if len(deduped) < len(downloaded):
        logger.info(f"Deduplicated {len(downloaded) - len(deduped)} duplicate filing(s).")
    downloaded = deduped

    # ── Step 3: Extract text + Analyse ───────────────────────────
    from pdf_extractor import extract_text
    from analyser      import analyse_transcript, is_transcript_content

    analyses = []
    failed   = []

    for item in downloaded:
        company  = item["company"]
        filepath = item["filepath"]

        text = extract_text(filepath)
        if not text:
            logger.warning(f"No text from {company}, skipping analysis.")
            failed.append(company)
            continue

        if not is_transcript_content(company, text):
            logger.info(f"Skipping {company} — zeroth-pass: not a transcript")
            continue

        analysis = analyse_transcript(company, text)
        analysis["ann_date"] = item["ann_date"]
        analysis["headline"] = item["headline"]
        analyses.append(analysis)

    logger.info(
        f"Analysis complete — {len(analyses)} succeeded, "
        f"{len(failed)} failed."
    )

    # ── Step 4: Save analyses ─────────────────────────────────────
    if analyses:
        label = f"{from_date}_to_{to_date}" if from_date and to_date else None
        save_analysis(analyses, label=label)

    # ── Step 5: Email digest ──────────────────────────────────────
    from emailer import send_digest
    ok = send_digest(analyses=analyses, failed=failed, from_date=from_date, to_date=to_date)
    if ok:
        logger.info("Digest email sent successfully.")
    else:
        logger.error("Digest email FAILED — check SMTP config.")

    logger.info("=" * 60)
    logger.info("Run complete.")
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Earnings Transcript Agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and download only — skip analysis and email",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        type=str,
        default=None,
        metavar="YYYYMMDD",
        help="Start date (default: yesterday)",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        type=str,
        default=None,
        metavar="YYYYMMDD",
        help="End date (default: today)",
    )
    parser.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=None,
        metavar="N",
        help="Process only the first N transcripts (useful for testing)",
    )
    parser.add_argument(
        "--source",
        dest="source",
        type=str,
        choices=["nse", "bse"],
        default="nse",
        help="Data source to use: nse (default) or bse",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run, from_date=args.from_date, to_date=args.to_date, limit=args.limit, source=args.source)
