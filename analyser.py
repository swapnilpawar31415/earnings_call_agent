# analyser.py
# Runs two-pass analysis on each transcript:
#   Pass 1 — structured answers to the standard question set
#   Pass 2 — freeform noteworthy assessment
#
# Default model: SLM (Qwen2.5-7B via Together AI or local Ollama)
# Use --model llm at the CLI to switch to Claude instead.

import logging
import httpx
import anthropic
from config import (
    ANTHROPIC_API_KEY,
    SLM_BASE_URL, SLM_API_KEY, SLM_MODEL,
)

logger = logging.getLogger(__name__)

LLM_MODEL   = "claude-sonnet-4-20250514"
HAIKU_MODEL = "claude-haiku-4-5-20251001"

_anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── Standard question set ────────────────────────────────────────────────────
STANDARD_QUESTIONS = """
FINANCIAL PERFORMANCE & OUTLOOK
1. What revenue and PAT numbers did management report for the quarter, and how did they compare to their own prior guidance (if mentioned)?
2. What is the guidance for the next quarter or full year on revenue, margins, and any key operating metric?
3. What were the key drivers cited for margin expansion or compression this quarter?
4. Were there any one-time or exceptional items that affected reported numbers?

BUSINESS & OPERATIONAL
5. What is the status of major capex or expansion plans — timelines, cost, and funding source?
6. What did management say about demand trends in their key segments or geographies?
7. Were there any new client wins, contract losses, product launches, or partnerships mentioned?

RISK & CONCERN SIGNALS
8. What risks or headwinds did management explicitly acknowledge?
9. Which analyst questions generated the most defensive or evasive responses?
10. Were there any topics where management's language was notably more cautious than prior quarters (if discernible from this transcript)?

CAPITAL ALLOCATION
11. What did management say about debt levels, working capital, or cash position?
12. Were dividends, buybacks, or fundraising plans discussed?
"""

PASS1_SYSTEM = """You are a senior equity research analyst. You are given an earnings call transcript.
Answer each numbered question precisely and concisely based ONLY on what is said in the transcript.
If a question is not addressed in the transcript, write "Not discussed."
Use INR figures as stated. Do not fabricate numbers. Keep each answer to 2-4 sentences."""

PASS2_SYSTEM = """You are a senior equity research analyst with 20 years of experience reading between the lines
of management commentary. You are given an earnings call transcript.

Identify 4-6 genuinely noteworthy observations that an analyst should not miss — things that are:
- Surprising, contradictory, or inconsistent with typical management optimism
- Repeated multiple times (suggesting high management emphasis)
- Answered evasively or deflected by management
- Potentially significant for the stock but underemphasised
- Changes in tone, language, or posture vs. what you'd expect

Be specific. Quote short phrases from the transcript to support each observation.
Format as a numbered list. Do not pad with generic observations."""


# ─── SLM call (Together AI / Ollama / any OpenAI-compatible endpoint) ─────────

def _call_slm(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
    """Call the configured SLM via OpenAI-compatible /chat/completions."""
    if not SLM_BASE_URL:
        raise RuntimeError(
            "SLM_BASE_URL is not set. Add it to .env or use --model llm."
        )
    try:
        r = httpx.post(
            f"{SLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {SLM_API_KEY}"},
            json={
                "model": SLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                "temperature": 0.0,
                "max_tokens":  max_tokens,
            },
            timeout=120.0,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"SLM call failed: {e}")
        return f"[Analysis failed: {e}]"


# ─── LLM call (Claude) ────────────────────────────────────────────────────────

def _call_llm(system_prompt: str, user_prompt: str, model: str = LLM_MODEL) -> str:
    """Call Claude via the Anthropic SDK."""
    try:
        msg = _anthropic_client.messages.create(
            model=model,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return msg.content[0].text
    except Exception as e:
        logger.error(f"Claude API call failed: {e}")
        return f"[Analysis failed: {e}]"


def _call(system_prompt: str, user_prompt: str, use_slm: bool, max_tokens: int = 2000) -> str:
    if use_slm:
        return _call_slm(system_prompt, user_prompt, max_tokens=max_tokens)
    return _call_llm(system_prompt, user_prompt)


# ─── Public API ───────────────────────────────────────────────────────────────

def is_transcript_content(company: str, text_sample: str, use_slm: bool = True) -> bool:
    """
    Zeroth-pass check: confirm the document is an actual earnings call transcript,
    not a participants list, intimation, or other regulatory filing.
    Returns True if the document contains real transcript content.
    """
    system = "You are a document classifier. Reply with only YES or NO."
    user   = (
        f"Does the following document excerpt contain an actual earnings call or "
        f"concall transcript with management commentary or analyst Q&A for {company}? "
        f"Answer YES if it has real spoken content. Answer NO if it is a participants "
        f"list, intimation notice, attendance record, or similar regulatory filing.\n\n"
        f"{text_sample[:3000]}"
    )
    try:
        answer = _call(system, user, use_slm=use_slm, max_tokens=10).strip().upper()
        return answer.startswith("YES")
    except Exception as e:
        logger.warning(f"Zeroth-pass check failed for {company}: {e} — assuming transcript")
        return True  # fail open: don't skip on API error


def analyse_transcript(company: str, transcript_text: str, use_slm: bool = True) -> dict:
    """
    Run both analysis passes on a transcript.
    Returns dict with 'standard_qa' and 'noteworthy' fields.
    """
    model_tag = f"SLM ({SLM_MODEL})" if use_slm else f"LLM ({LLM_MODEL})"
    logger.info(f"Analysing transcript for {company} [{model_tag}]...")

    # Pass 1 — Standard Q&A
    pass1_user = f"""COMPANY: {company}

TRANSCRIPT:
{transcript_text}

---
Answer each of the following questions based on the transcript above:

{STANDARD_QUESTIONS}"""

    standard_qa = _call(PASS1_SYSTEM, pass1_user, use_slm=use_slm)
    logger.info(f"Pass 1 complete for {company}")

    # Pass 2 — Freeform noteworthy observations
    pass2_user = f"""COMPANY: {company}

TRANSCRIPT:
{transcript_text}

---
Provide your noteworthy analyst observations for this earnings call."""

    noteworthy = _call(PASS2_SYSTEM, pass2_user, use_slm=use_slm)
    logger.info(f"Pass 2 complete for {company}")

    return {
        "company":     company,
        "standard_qa": standard_qa,
        "noteworthy":  noteworthy,
        "model":       model_tag,
    }
