#!/usr/bin/env python3
"""
Daily sourcing pipeline — runs in GitHub Actions (full internet, no MCP needed).

Data sources:
  - EU-Startups + TechCrunch RSS feeds  (regex extraction; Claude NLP if ANTHROPIC_API_KEY set)
  - data/pitchbook_results.json         (written by run_pitchbook_agent.py on this server)
  - Grata API                           (if GRATA_API_KEY is set)
"""
import json
import logging
import sys
from pathlib import Path

from config import THESIS
from models import Company
from tools.crm_filter import filter_known_companies
from tools.grata import GrataClient
from tools.news import run_news_agent
from tools.slack import send_digest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("sourcing")

_PB_RESULTS = Path(__file__).parent / "data" / "pitchbook_results.json"


def _load_pitchbook_results() -> list[Company]:
    """Load companies written by the server-side PitchBook agent."""
    if not _PB_RESULTS.exists():
        logger.info("No data/pitchbook_results.json — PitchBook source skipped")
        return []
    try:
        payload = json.loads(_PB_RESULTS.read_text())
        companies = []
        for item in payload.get("companies", []):
            companies.append(Company(
                name=item.get("name") or "Unknown",
                country=item.get("country") or "Unknown",
                sector=item.get("sector") or "Software",
                description=item.get("description") or "",
                total_funding_eur=item.get("total_funding_eur"),
                last_round_type=item.get("last_round_type"),
                last_round_amount_eur=item.get("last_round_amount_eur"),
                last_round_date=item.get("last_round_date"),
                headcount=item.get("headcount"),
                founded_year=item.get("founded_year"),
                website=item.get("website"),
                pitchbook_url=item.get("pitchbook_url"),
                source="pitchbook",
                source_url=item.get("pitchbook_url"),
            ))
        logger.info("Loaded %d companies from PitchBook results file", len(companies))
        return companies
    except Exception as e:
        logger.warning("Failed to load PitchBook results: %s", e)
        return []


def _score(company: Company) -> Company:
    score = 0
    breakdown: dict[str, int] = {}

    geo_values = set(THESIS["geographies"].values())
    if company.country in geo_values:
        score += 20
        breakdown["geography"] = 20

    sector_kws = [s.lower() for s in THESIS["sectors"]]
    combined = f"{company.sector} {company.description}".lower()
    if any(kw in combined for kw in sector_kws):
        score += 20
        breakdown["sector"] = 20

    total = company.total_funding_eur or 0
    pts = 25 if total == 0 else 20 if total <= 10_000_000 else 10 if total <= THESIS["max_total_funding_eur"] else 0
    score += pts
    breakdown["funding"] = pts

    if company.last_round_type in THESIS["deal_types_include"]:
        score += 10
        breakdown["round_type"] = 10

    hc = company.headcount or 0
    if THESIS["min_headcount"] <= hc <= 80:
        score += 25
        breakdown["headcount"] = 25
    elif 80 < hc <= THESIS["max_headcount"]:
        score += 15
        breakdown["headcount"] = 15

    company.score = min(score, 100)
    company.score_breakdown = breakdown
    return company


def _dedup(companies: list[Company]) -> list[Company]:
    seen: set[str] = set()
    unique: list[Company] = []
    for c in companies:
        k = c.name.lower().strip()
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return unique


def main() -> None:
    logger.info("═══ Daily sourcing pipeline starting ═══")

    grata = GrataClient()

    # 1. Collect
    logger.info("Phase 1: collecting from all sources")
    news_cos = run_news_agent(hours_back=24)
    pb_cos = _load_pitchbook_results()
    grata_cos = grata.search_companies()

    # 2. Merge + deduplicate
    all_companies = _dedup(news_cos + pb_cos + grata_cos)
    logger.info("Total unique companies: %d", len(all_companies))

    # 3. Score
    scored = [_score(c) for c in all_companies]

    # 4. Filter
    qualified = [
        c for c in scored
        if (c.score or 0) >= THESIS["min_score_threshold"]
        and (c.total_funding_eur is None or c.total_funding_eur <= THESIS["max_total_funding_eur"])
        and c.last_round_type not in THESIS["deal_types_exclude"]
    ]
    qualified.sort(key=lambda c: c.score or 0, reverse=True)
    logger.info("Qualified: %d", len(qualified))

    # 5. Portfolio dedup
    new_companies, skipped = filter_known_companies(qualified)
    logger.info("Net new: %d  |  Skipped: %d", len(new_companies), skipped)

    # 6. Slack digest
    send_digest(companies=new_companies, skipped_crm=skipped, total_found=len(all_companies))
    logger.info("═══ Pipeline complete ═══")


if __name__ == "__main__":
    main()
