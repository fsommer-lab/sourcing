#!/usr/bin/env python3
"""
Daily sourcing pipeline — runs on the Claude Code server where
the claude CLI and PitchBook MCP are available.
"""
import logging
import sys

from config import THESIS
from models import Company
from tools.crm_filter import filter_known_companies
from tools.grata import GrataClient
from tools.news import run_news_agent
from tools.pitchbook import enrich_from_pitchbook, run_pitchbook_news_search
from tools.slack import send_digest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("sourcing")


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
    pb_cos = run_pitchbook_news_search()
    grata_cos = grata.search_companies()

    # 2. Enrich news companies with PitchBook data
    logger.info("Phase 2: enriching %d news companies via PitchBook", len(news_cos))
    news_cos = [enrich_from_pitchbook(c) for c in news_cos]

    # 3. Merge + deduplicate
    all_companies = _dedup(news_cos + pb_cos + grata_cos)
    logger.info("Total unique companies: %d", len(all_companies))

    # 4. Score
    scored = [_score(c) for c in all_companies]

    # 5. Filter
    qualified = [
        c for c in scored
        if (c.score or 0) >= THESIS["min_score_threshold"]
        and (c.total_funding_eur is None or c.total_funding_eur <= THESIS["max_total_funding_eur"])
        and c.last_round_type not in THESIS["deal_types_exclude"]
    ]
    qualified.sort(key=lambda c: c.score or 0, reverse=True)
    logger.info("Qualified: %d", len(qualified))

    # 6. Portfolio dedup
    new_companies, skipped = filter_known_companies(qualified)
    logger.info("Net new: %d  |  Skipped: %d", len(new_companies), skipped)

    # 7. Slack digest
    send_digest(companies=new_companies, skipped_crm=skipped, total_found=len(all_companies))
    logger.info("═══ Pipeline complete ═══")


if __name__ == "__main__":
    main()
