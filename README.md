# Earnings Call Transcript Agent

Daily agent that finds BSE500 earnings call transcripts filed the previous day,
analyses them with Claude, and emails a digest to your team.

---

## Setup (one-time, ~10 minutes)

### 1. Prerequisites
```bash
python3 --version   # need 3.10+
pip install requests pdfplumber anthropic
```

### 2. Get your API keys

**Anthropic API key**
- Go to https://console.anthropic.com → API Keys → Create key
- Copy the key starting with `sk-ant-...`

**Gmail App Password** (do NOT use your main Gmail password)
- Go to https://myaccount.google.com/apppasswords
- Select "Mail" + your device → Generate
- Copy the 16-character password

### 3. Edit config.py
```python
ANTHROPIC_API_KEY  = "sk-ant-YOUR_KEY_HERE"
GMAIL_SENDER       = "your.email@gmail.com"
GMAIL_APP_PASSWORD = "xxxx xxxx xxxx xxxx"
RECIPIENT_EMAILS   = ["you@firm.com", "colleague@firm.com"]
STORAGE_BASE_DIR   = "./transcripts"   # or full path like "/data/earnings"
```

### 4. Test with a dry run
```bash
cd earnings_agent
python main.py --dry-run
```
This fetches the announcement list and downloads PDFs only — no email sent, no API cost.

### 5. Run for a specific past date (to verify end-to-end)
```bash
python main.py --date 20250228
```
Pick a date you know had concall filings.

### 6. Schedule with cron (daily at 7am IST)
```bash
crontab -e
```
Add this line:
```
0 7 * * * cd /full/path/to/earnings_agent && python main.py >> logs/cron.log 2>&1
```

---

## File structure after first run

```
earnings_agent/
├── main.py              ← orchestrator (run this)
├── config.py            ← YOUR SETTINGS (edit before running)
├── bse_fetcher.py       ← BSE API poller + PDF downloader
├── pdf_extractor.py     ← PDF → text
├── analyser.py          ← Claude two-pass analysis
├── emailer.py           ← Gmail digest sender
├── bse500_codes.py      ← BSE500 scrip code list
├── transcripts/
│   └── 2025-03-06/
│       ├── Infosys_500209_12345.pdf
│       ├── TCS_532540_67890.pdf
│       └── analyses.json     ← structured analysis output
└── logs/
    └── run_20250306_070012.log
```

---

## Updating the BSE500 list

BSE publishes the current BSE500 constituents at:
https://www.bseindia.com/indices/IndexArchiveData.html

The `bse500_codes.py` file contains the scrip codes. Refresh quarterly by
downloading the latest constituent list and updating the `BSE500_CODES` list.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| No transcripts found even on known filing days | BSE API may have changed — check `bse_fetcher.py` TRANSCRIPT_KEYWORDS |
| PDF text extraction blank | Transcript is a scanned image PDF — needs OCR (add `pytesseract`) |
| Email fails with auth error | Make sure you're using App Password, not Gmail password |
| Claude API error | Check API key and account credit at console.anthropic.com |

---

## Switching to Claude Code for iteration

Once this is running, use Claude Code to:
- Add new analysis questions (`analyser.py`)
- Add OCR support for scanned PDFs
- Add a watchlist override (specific companies always included)
- Push analyses to a database or Google Sheets
