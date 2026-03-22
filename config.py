# config.py
# Earnings Call Transcript Agent - Configuration
# Secrets are loaded from .env (never commit .env)

import os
from dotenv import load_dotenv

load_dotenv()

# ─── Anthropic ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# ─── Email (Gmail SMTP) ───────────────────────────────────────────────────────
# Use an App Password, not your main Gmail password.
# Generate at: https://myaccount.google.com/apppasswords
GMAIL_SENDER      = os.environ["GMAIL_SENDER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

RECIPIENT_EMAILS = [
    "spawar@asqi.in",
    "swapnilpawar31415@gmail.com",
    # add more as needed
]

# ─── Storage ─────────────────────────────────────────────────────────────────
# Directory where transcripts and analysis are saved
STORAGE_BASE_DIR = "./transcripts"   # relative to where you run the script

# ─── Agent behaviour ─────────────────────────────────────────────────────────
# How many days back to look for transcripts (1 = yesterday)
LOOKBACK_DAYS = 1

# Max transcript text length sent to Claude (in characters).
# ~150,000 chars ≈ a very long concall. Raise if needed.
MAX_TRANSCRIPT_CHARS = 150_000

# BSE announcement categories that contain concall transcripts
# BSE uses these subcategory strings in their API
TRANSCRIPT_KEYWORDS = [
    "transcript",
    "concall",
    "earnings call",
    "investor meet",
    "analyst meet",
    "conference call",
]
