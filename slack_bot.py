#!/usr/bin/env python3
"""
Slack bot for Spectrum Equity VC portfolio sourcing — Socket Mode.

Uses Slack Socket Mode so NO public URL or ngrok is needed.
The bot connects outbound to Slack; works from any machine, any network.

Setup (one-time):
1. api.slack.com/apps → your app → Socket Mode → Enable Socket Mode
2. Under "App-Level Tokens" → Generate Token → scope: connections:write
   → copy token (xapp-...) → SLACK_APP_TOKEN in .env
3. OAuth & Permissions → Bot Token Scopes → add: commands, chat:write
4. Install app to workspace → copy Bot Token (xoxb-...) → SLACK_BOT_TOKEN in .env
5. Slash Commands → Create: /sourcing  (no URL needed in Socket Mode)
6. Re-install app if prompted

Run:
    python3 slack_bot.py
"""
import logging
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools.claude_cli import call_claude, parse_json
from vc_portfolio_sourcing import (
    _PORTFOLIO_PROMPT,
    _to_float,
    score_company,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("slack_bot")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")


# ── PitchBook lookup ──────────────────────────────────────────────────────────

def _fetch_and_score(fund_name: str) -> list[tuple[int, list[str], dict]]:
    output = call_claude(_PORTFOLIO_PROMPT.format(fund_name=fund_name), timeout=300)
    if not output:
        return []
    data = parse_json(output)
    if not isinstance(data, list):
        return []
    scored = [(*(score_company(c),), c) for c in data]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


# ── Slack message formatting ──────────────────────────────────────────────────

def _score_bar(score: int) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


def _fmt_usd(value) -> str:
    v = _to_float(value)
    return f"${v/1e6:.1f}M" if v else "N/A"


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
        display = website.replace("https://", "").replace("http://", "").rstrip("/")
        name_line += f"  <{website}|{display}>"

    detail = f"_{sector}_ · {stage} · Last: {last_round} · Total: {total} · {hc_str}"
    if city:
        detail += f" · {city}"

    hits     = "  ".join(f"✓ {r}" for r in reasons if not r.startswith("⚠"))
    warnings = "  ".join(r for r in reasons if r.startswith("⚠"))

    lines = [name_line, detail, f"`{_score_bar(score)}  {score}/100`"]
    if desc:     lines.append(desc)
    if hits:     lines.append(hits)
    if warnings: lines.append(f"_{warnings}_")

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        {"type": "divider"},
    ]


def _build_blocks(fund_name: str, scored: list) -> list[dict]:
    relevant = [(s, r, c) for s, r, c in scored if s >= 40]
    skipped  = [(s, r, c) for s, r, c in scored if s < 40]

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"VC Portfolio Sourcing — {fund_name}"}},
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
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"No portfolio companies found for *{fund_name}* on PitchBook."}})
        return blocks

    if relevant:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": "*Relevant companies* (score ≥ 40)"}})
        for rank, (score, reasons, c) in enumerate(relevant, start=1):
            blocks.extend(_company_block(rank, score, reasons, c))
            if len(blocks) >= 45:
                blocks.append({"type": "section", "text": {"type": "mrkdwn",
                    "text": f"_… and {len(relevant) - rank} more._"}})
                break

    if skipped:
        names = ", ".join(c.get("name", "?") for _, _, c in skipped[:10])
        suffix = f" + {len(skipped)-10} more" if len(skipped) > 10 else ""
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"*Below threshold:* {names}{suffix}"}})

    return blocks


# ── Background worker ─────────────────────────────────────────────────────────

def _run_search(fund_name: str, say) -> None:
    logger.info("Searching PitchBook for: %s", fund_name)
    try:
        scored = _fetch_and_score(fund_name)
        blocks = _build_blocks(fund_name, scored)
        say(blocks=blocks, text=f"Results for {fund_name}")
    except Exception as e:
        logger.error("Search failed: %s", e)
        say(text=f"Error searching for *{fund_name}*: {e}")


# ── Slack app ─────────────────────────────────────────────────────────────────

def main() -> None:
    if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
        print(
            "ERROR: Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN in your .env file.\n"
            "See the setup steps at the top of this file."
        )
        sys.exit(1)

    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError:
        print("Run: pip install slack-bolt")
        sys.exit(1)

    bolt = App(token=SLACK_BOT_TOKEN)

    @bolt.command("/sourcing")
    def handle_sourcing(ack, say, command):
        fund_name = (command.get("text") or "").strip()
        if not fund_name:
            ack("Usage: `/sourcing <VC fund name>`\nExample: `/sourcing Bessemer Venture Partners`")
            return
        ack(f"Searching PitchBook for *{fund_name}*… results coming shortly.")
        threading.Thread(target=_run_search, args=(fund_name, say), daemon=True).start()

    logger.info("Starting Slack bot in Socket Mode — no public URL needed")
    SocketModeHandler(bolt, SLACK_APP_TOKEN).start()


if __name__ == "__main__":
    main()
