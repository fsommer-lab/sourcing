import json
import logging
from datetime import date
from typing import Optional

import requests

from config import SLACK_WEBHOOK_URL
from models import Company

logger = logging.getLogger(__name__)

_SOURCE_LABEL = {
    "pitchbook": "PitchBook",
    "grata": "Grata",
    "EU-Startups Funding": "EU-Startups",
    "TechCrunch Europe": "TechCrunch EU",
    "TechCrunch Startups": "TechCrunch",
}

_SOURCE_EMOJI = {
    "pitchbook": "📊",
    "grata": "🔍",
    "EU-Startups Funding": "🇪🇺",
    "TechCrunch Europe": "📰",
    "TechCrunch Startups": "📰",
}


def _eur(amount: float) -> str:
    if amount >= 1_000_000:
        return f"€{amount / 1_000_000:.1f}M"
    return f"€{amount / 1_000:.0f}K"


def _score_bar(score: float) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled) + f"  {score:.0f}/100"


def _funding_line(company: Company) -> str:
    if company.last_round_amount_eur and company.last_round_type:
        line = f"{company.last_round_type}: {_eur(company.last_round_amount_eur)}"
        if (
            company.total_funding_eur
            and company.total_funding_eur != company.last_round_amount_eur
        ):
            line += f" · total {_eur(company.total_funding_eur)}"
        return line
    if company.total_funding_eur:
        return f"Total raised: {_eur(company.total_funding_eur)}"
    return "Bootstrapped / funding not disclosed"


def _company_block(company: Company, rank: int) -> list[dict]:
    emoji = _SOURCE_EMOJI.get(company.source, "📌")
    source_label = _SOURCE_LABEL.get(company.source, company.source)
    hc = f"{company.headcount} employees" if company.headcount else "headcount unknown"
    desc = company.description or ""
    if len(desc) > 220:
        desc = desc[:220] + "…"

    lines = [
        f"*{rank}. {company.name}*  ·  {company.country}  {emoji} _{source_label}_",
        f"_{company.sector}_  ·  {hc}  ·  {_funding_line(company)}",
        f"`{_score_bar(company.score or 0)}`",
    ]
    if desc:
        lines.append(desc)
    if company.article_title:
        lines.append(f"📄 _{company.article_title}_")

    accessory: Optional[dict] = None
    if company.source_url:
        accessory = {
            "type": "button",
            "text": {"type": "plain_text", "text": "View →"},
            "url": company.source_url,
        }

    block: dict = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(lines)},
    }
    if accessory:
        block["accessory"] = accessory

    return [block, {"type": "divider"}]


def send_digest(
    companies: list[Company],
    skipped_crm: int = 0,
    total_found: int = 0,
) -> bool:
    if not SLACK_WEBHOOK_URL:
        logger.warning("SLACK_WEBHOOK_URL not set — skipping Slack notification")
        return False

    today = date.today().strftime("%B %d, %Y")
    net_new = len(companies)

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🔭 Daily Sourcing Digest — {today}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Scanned:* {total_found} companies"},
                {"type": "mrkdwn", "text": f"*Already in pipeline:* {skipped_crm}"},
                {"type": "mrkdwn", "text": f"*Net new:* {net_new}"},
                {
                    "type": "mrkdwn",
                    "text": (
                        f"*Top score:* {max(c.score or 0 for c in companies):.0f}/100"
                        if companies
                        else "*Top score:* —"
                    ),
                },
            ],
        },
        {"type": "divider"},
    ]

    if not companies:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "No new companies matching the thesis today. Check back tomorrow.",
            },
        })
    else:
        for rank, company in enumerate(companies, start=1):
            blocks.extend(_company_block(company, rank))
            if len(blocks) >= 48:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"_… and {net_new - rank} more. Trigger a manual run in GitHub Actions for full output._",
                    },
                })
                break

    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            data=json.dumps({"blocks": blocks}),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Slack digest sent: %d companies", net_new)
        return True
    except Exception as e:
        logger.error("Failed to send Slack digest: %s", e)
        return False
