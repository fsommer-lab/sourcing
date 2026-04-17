#!/usr/bin/env python3
"""
Daily sourcing pipeline for European growth equity deal flow.

Sources
-------
- EU-Startups + TechCrunch   RSS feeds → Claude NLP extracts company data
- PitchBook                  Via local claude CLI + PitchBook MCP (no Data API needed)
- Grata                      Company search API

Pipeline
--------
collect → enrich → deduplicate → score → filter → CRM dedup → Slack digest
"""
import logging
import sys

import anthropic

from config import ANTHROPIC_API_KEY, THESIS
from models import Company
from tools.grata import GrataClient
from tools.news import run_news_agent
from tools.pitchbook import enrich_from_pitchbook, run_pitchbook_news_search
from tools.salesforce_client import SalesforceClient
from tools.slack import send_digest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("sourcing")


# ── Scoring ────────────────────────────────────────────────────────────────────

def _score(company: Company) -> Company:
    """
    Score 0–100 against the investment thesis.

    Geography match      20 pts
    Sector match         20 pts
    Funding fit          25 pts  (bootstrapped) / 20 (≤€10M) / 10 (≤€20M)
    Round type           10 pts
    Headcount fit        25 pts  (25-80 ideal) / 15 (81-300)
    """
    score = 0
    breakdown: dict[str, int] = {}

    geo_values = set(THESIS["geographies"].values())
    geo_keys = set(THESIS["geographies"].keys())
    if company.country in geo_values or company.country in geo_keys:
        score += 20
        breakdown["geography"] = 20

    sector_kws = [s.lower() for s in THESIS["sectors"]]
    combined = f"{company.sector} {company.description}".lower()
    if any(kw in combined for kw in sector_kws):
        score += 20
        breakdown["sector"] = 20

    total = company.total_funding_eur or 0
    if total == 0:
        pts = 25
    elif total <= 10_000_000:
        pts = 20
    elif total <= THESIS["max_total_funding_eur"]:
        pts = 10
    else:
        pts = 0
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


# ── Deduplication ─────────────────────────────────────────────────────────────

def _dedup(companies: list[Company]) -> list[Company]:
    seen: set[str] = set()
    unique: list[Company] = []
    for c in companies:
        key = c.name.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


# ── Main pipeline ─────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("═══ Daily sourcing pipeline starting ═══")

    anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    grata = GrataClient()
    sf = SalesforceClient()

    # 1. Collect from all sources in parallel (conceptually — sequential here)
    logger.info("Phase 1: collecting from all sources")
    news_cos = run_news_agent(anthropic_client)
    pb_cos = run_pitchbook_news_search()
    grata_cos = grata.search_companies()

    # 2. Enrich news-found companies with PitchBook data (fills headcount, funding gaps)
    logger.info("Phase 2: enriching %d news companies via PitchBook", len(news_cos))
    news_cos = [enrich_from_pitchbook(c) for c in news_cos]

    # 3. Merge + deduplicate
    all_companies = _dedup(news_cos + pb_cos + grata_cos)
    logger.info("Total unique companies: %d", len(all_companies))

    # 4. Score
    scored = [_score(c) for c in all_companies]

    # 5. Filter: score threshold + funding cap + no late-stage rounds
    qualified = [
        c for c in scored
        if (c.score or 0) >= THESIS["min_score_threshold"]
        and (c.total_funding_eur is None or c.total_funding_eur <= THESIS["max_total_funding_eur"])
        and c.last_round_type not in THESIS["deal_types_exclude"]
    ]
    qualified.sort(key=lambda c: c.score or 0, reverse=True)
    logger.info("Qualified (score ≥ %d): %d", THESIS["min_score_threshold"], len(qualified))

    # 6. CRM dedup
    new_companies = sf.filter_new(qualified)
    skipped = len(qualified) - len(new_companies)
    logger.info("Net new (not in Salesforce): %d", len(new_companies))

    # 7. Slack digest
    send_digest(
        companies=new_companies,
        skipped_crm=skipped,
        total_found=len(all_companies),
    )

    logger.info("═══ Pipeline complete ═══")


if __name__ == "__main__":
    main()
