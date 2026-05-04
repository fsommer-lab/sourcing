#!/usr/bin/env python3
"""
Slack bot for Spectrum Equity VC portfolio sourcing.

Exposes a slash command endpoint. When you type:
    /sourcing Bessemer Venture Partners
    /sourcing https://www.insightpartners.com

…it looks up the fund's PitchBook portfolio and replies with companies
that match Spectrum's thesis (stage, business model, funding, headcount).

Setup:
1. Create a Slack app at https://api.slack.com/apps
2. Add a Slash Command: /sourcing → https://<your-server>/sourcing
3. Enable "Escape channels, users, and links" OFF
4. Copy Signing Secret → SLACK_SIGNING_SECRET in .env
5. Run: python3 slack_bot.py

Deploy tip: expose with `ngrok http 3000` during development.
"""
import hashlib
import hmac
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import requests
from flask import Flask, Response, request

sys.path.insert(0, str(Path(__file__).parent))

from tools.claude_cli import call_claude, parse_json
from vc_portfolio_sourcing import (
    SPECTRUM_THESIS,
    _PORTFOLIO_PROMPT,
    _to_float,
    _to_int,
    score_company,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("slack_bot")

app = Flask(__name__)

SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
PORT = int(os.getenv("PORT", 3000))


# ── Slack request verification ────────────────────────────────────────────────

def _verify_slack_signature(req: request) -> bool:
    """Reject requests that didn't come from Slack."""
    if not SLACK_SIGNING_SECRET:
        logger.warning("SLACK_SIGNING_SECRET not set — skipping verification (dev mode)")
        return True

    timestamp = req.headers.get("X-Slack-Request-Timestamp", "")
    if abs(time.time() - float(timestamp)) > 300:
        return False

    sig_basestring = f"v0:{timestamp}:{req.get_data(as_text=True)}"
    expected = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        sig_basestring.encode(),
        hashlib.sha256,
    ).hexdigest()
    received = req.headers.get("X-Slack-Signature", "")
    return hmac.compare_digest(expected, received)


# ── PitchBook lookup ──────────────────────────────────────────────────────────

def _fetch_and_score(fund_name: str) -> list[tuple[int, list[str], dict]]:
    """Return list of (score, reasons, company_dict) sorted descending."""
    output = call_claude(_PORTFOLIO_PROMPT.format(fund_name=fund_name), timeout=300)
    if not output:
        return []
    data = parse_json(output)
    if not isinstance(data, list):
        return []

    scored = []
    for c in data:
        score, reasons = score_company(c)
        scored.append((score, reasons, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


# ── Slack message formatting ──────────────────────────────────────────────────

def _score_bar(score: int) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


def _fmt_usd(value) -> str:
    v = _to_float(value)
    if v is None:
        return "N/A"
    return f"${v/1e6:.1f}M"


def _company_block(rank: int, score: int, reasons: list[str], c: dict) -> list[dict]:
    name = c.get("name") or "Unknown"
    website = c.get("website") or c.get("pitchbook_url") or ""
    desc = (c.get("description") or "")[:200]
    stage = c.get("last_round_type") or "N/A"
    total = _fmt_usd(c.get("total_funding_usd"))
    last_round = _fmt_usd(c.get("last_round_amount_usd"))
    headcount = c.get("headcount")
    hc_str = f"{headcount} employees" if headcount else "headcount N/A"
    sector = c.get("sector") or "N/A"
    city = c.get("hq_city") or c.get("hq_country") or ""

    name_line = f"*{rank}. {name}*"
    if website:
        name_line += f"  <{website}|{website.replace('https://', '').replace('http://', '').rstrip('/')}>"

    detail = f"_{sector}_ · {stage} · Last: {last_round} · Total: {total} · {hc_str}"
    if city:
        detail += f" · {city}"

    reason_text = "  ".join(f"✓ {r}" for r in reasons if not r.startswith("⚠"))
    penalty_text = "  ".join(r for r in reasons if r.startswith("⚠"))

    text_lines = [name_line, detail, f"`{_score_bar(score)}  {score}/100`"]
    if desc:
        text_lines.append(desc)
    if reason_text:
        text_lines.append(reason_text)
    if penalty_text:
        text_lines.append(f"_{penalty_text}_")

    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(text_lines)},
        },
        {"type": "divider"},
    ]


def _build_response_blocks(fund_name: str, scored: list) -> list[dict]:
    relevant = [(s, r, c) for s, r, c in scored if s >= 40]
    skipped  = [(s, r, c) for s, r, c in scored if s < 40]

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"VC Portfolio Sourcing — {fund_name}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Portfolio scanned:* {len(scored)}"},
                {"type": "mrkdwn", "text": f"*Relevant for Spectrum:* {len(relevant)}"},
            ],
        },
        {"type": "divider"},
    ]

    if not scored:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"No portfolio companies found for *{fund_name}* on PitchBook."},
        })
        return blocks

    if relevant:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Relevant companies* (score ≥ 40)"},
        })
        for rank, (score, reasons, c) in enumerate(relevant, start=1):
            blocks.extend(_company_block(rank, score, reasons, c))
            if len(blocks) >= 45:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"_… and {len(relevant) - rank} more relevant companies. Run with `--json` for full output._",
                    },
                })
                break

    if skipped:
        names = ", ".join(c.get("name", "?") for _, _, c in skipped[:10])
        suffix = f" + {len(skipped)-10} more" if len(skipped) > 10 else ""
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Below threshold:* {names}{suffix}",
            },
        })

    return blocks


# ── Background worker ─────────────────────────────────────────────────────────

def _run_search(fund_name: str, response_url: str) -> None:
    logger.info("Starting PitchBook search for: %s", fund_name)
    try:
        scored = _fetch_and_score(fund_name)
        blocks = _build_response_blocks(fund_name, scored)
        payload = {"response_type": "in_channel", "blocks": blocks}
    except Exception as e:
        logger.error("Search failed: %s", e)
        payload = {
            "response_type": "ephemeral",
            "text": f"Error searching for *{fund_name}*: {e}",
        }

    try:
        resp = requests.post(
            response_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Posted results to Slack for: %s", fund_name)
    except Exception as e:
        logger.error("Failed to post Slack response: %s", e)


# ── Flask route ───────────────────────────────────────────────────────────────

@app.route("/sourcing", methods=["POST"])
def sourcing():
    if not _verify_slack_signature(request):
        return Response("Unauthorized", status=401)

    fund_name = (request.form.get("text") or "").strip()
    response_url = request.form.get("response_url", "")
    user = request.form.get("user_name", "someone")

    if not fund_name:
        return Response(
            json.dumps({
                "response_type": "ephemeral",
                "text": "Usage: `/sourcing <VC fund name or website>`\nExample: `/sourcing Bessemer Venture Partners`",
            }),
            content_type="application/json",
        )

    # Ack immediately — Slack requires a response within 3 seconds
    threading.Thread(
        target=_run_search,
        args=(fund_name, response_url),
        daemon=True,
    ).start()

    return Response(
        json.dumps({
            "response_type": "in_channel",
            "text": f"Searching PitchBook for *{fund_name}* portfolio… I'll post results here shortly.",
        }),
        content_type="application/json",
    )


@app.route("/health", methods=["GET"])
def health():
    return Response("ok", status=200)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not SLACK_SIGNING_SECRET:
        logger.warning("SLACK_SIGNING_SECRET not set — run in dev mode only")
    logger.info("Starting Slack bot on port %d", PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)
