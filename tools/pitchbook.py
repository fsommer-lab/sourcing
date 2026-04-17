import logging
from typing import Optional

from models import Company
from tools.claude_cli import call_claude, parse_json

logger = logging.getLogger(__name__)

# ── Prompts ────────────────────────────────────────────────────────────────────

_NEWS_SEARCH_PROMPT = """\
Use the pitchbook_get_news_analysis tool with collection PITCHBOOK_NEWS and this query:
"European software SaaS AI data analytics B2B companies raised pre-seed seed series A funding past 7 days"

From the results, keep only companies that match ALL of:
- Country: Germany, Austria, Switzerland, United Kingdom, Sweden, Norway, Denmark,
  Finland, Netherlands, Belgium, France, Italy, Spain, or Israel
- Round type: Pre-Seed, Seed, or Series A  (exclude Series B+)
- Sector: Software, SaaS, AI, Machine Learning, Data, Analytics, B2B tech
- Total funding raised so far: €20M or less

Return ONLY a JSON array — no markdown, no explanation:
[{
  "name": "company name",
  "country": "full country name",
  "sector": "primary sector",
  "description": "2-3 sentences on what they do",
  "total_funding_eur": number or null,
  "last_round_type": "Pre-Seed|Seed|Series A or null",
  "last_round_amount_eur": number or null,
  "last_round_date": "YYYY-MM-DD or null",
  "website": "URL or null",
  "pitchbook_url": "URL or null"
}]

If nothing matches, return: []\
"""

_ENRICH_PROMPT = """\
Look up "{name}" on PitchBook:
1. Call pitchbook_search with name="{name}"
2. Call pitchbook_get_profile on the first matching PBID
3. Call pitchbook_get_company_deals on the same PBID

Return ONLY a JSON object — no markdown, no explanation:
{{
  "found": true or false,
  "total_funding_eur": number or null,
  "last_round_type": "string or null",
  "last_round_amount_eur": number or null,
  "headcount": number or null,
  "founded_year": number or null,
  "pitchbook_url": "URL or null",
  "description": "string or null"
}}\
"""


# ── Public API ─────────────────────────────────────────────────────────────────

def run_pitchbook_news_search() -> list[Company]:
    """
    Use the PitchBook MCP (via the local claude CLI) to pull recently funded
    European tech companies from PitchBook's news feed.
    """
    logger.info("PitchBook: querying recent European funding news via claude CLI…")
    output = call_claude(_NEWS_SEARCH_PROMPT)
    if not output:
        return []

    data = parse_json(output)
    if not isinstance(data, list):
        logger.warning("PitchBook news search: unexpected response format")
        return []

    companies: list[Company] = []
    for item in data:
        try:
            companies.append(Company(
                name=item.get("name") or "Unknown",
                country=item.get("country") or "Unknown",
                sector=item.get("sector") or "Software",
                description=item.get("description") or "",
                total_funding_eur=_f(item.get("total_funding_eur")),
                last_round_type=item.get("last_round_type"),
                last_round_amount_eur=_f(item.get("last_round_amount_eur")),
                last_round_date=item.get("last_round_date"),
                website=item.get("website"),
                pitchbook_url=item.get("pitchbook_url"),
                source="pitchbook",
                source_url=item.get("pitchbook_url"),
            ))
        except Exception as e:
            logger.warning("Failed to parse PitchBook item: %s", e)

    logger.info("PitchBook: %d companies found", len(companies))
    return companies


def enrich_from_pitchbook(company: Company) -> Company:
    """
    Look up a company on PitchBook and fill in any missing fields
    (headcount, total funding, PitchBook URL, etc.).
    Only called for companies found via news where data may be incomplete.
    """
    logger.info("PitchBook enrichment: %s", company.name)
    output = call_claude(_ENRICH_PROMPT.format(name=company.name))
    if not output:
        return company

    data = parse_json(output)
    if not isinstance(data, dict) or not data.get("found"):
        return company

    # Fill gaps — never overwrite data we already have
    if not company.total_funding_eur:
        company.total_funding_eur = _f(data.get("total_funding_eur"))
    if not company.last_round_type:
        company.last_round_type = data.get("last_round_type")
    if not company.headcount:
        company.headcount = data.get("headcount")
    if not company.founded_year:
        company.founded_year = data.get("founded_year")
    if not company.pitchbook_url and data.get("pitchbook_url"):
        company.pitchbook_url = data["pitchbook_url"]
        company.source_url = company.source_url or data["pitchbook_url"]
    if not company.description and data.get("description"):
        company.description = data["description"]

    return company


def _f(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
