#!/usr/bin/env python3
"""
Server-side PitchBook agent.

Runs `claude -p` (which has PitchBook MCP) to search for recently funded
European companies, saves results to data/pitchbook_results.json, then
commits and pushes to GitHub so the GitHub Actions workflow picks it up.

Schedule via cron on this server (runs before 7am UTC):
  0 6 * * 1-5  cd /home/user/sourcing && python3 run_pitchbook_agent.py
"""
import json
import logging
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools.pitchbook import run_pitchbook_news_search

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pitchbook_agent")

OUTPUT_FILE = Path(__file__).parent / "data" / "pitchbook_results.json"


def main() -> None:
    logger.info("Running PitchBook agent via claude CLI…")

    companies = run_pitchbook_news_search()

    if not companies:
        logger.info("No PitchBook results — not committing")
        return

    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    payload = {
        "date": date.today().isoformat(),
        "companies": [
            {
                "name": c.name,
                "country": c.country,
                "sector": c.sector,
                "description": c.description,
                "total_funding_eur": c.total_funding_eur,
                "last_round_type": c.last_round_type,
                "last_round_amount_eur": c.last_round_amount_eur,
                "last_round_date": c.last_round_date,
                "headcount": c.headcount,
                "founded_year": c.founded_year,
                "website": c.website,
                "pitchbook_url": c.pitchbook_url,
            }
            for c in companies
        ],
    }
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("Wrote %d companies to %s", len(companies), OUTPUT_FILE)

    # Commit and push so GitHub Actions picks it up
    repo_root = Path(__file__).parent
    try:
        subprocess.run(
            ["git", "add", str(OUTPUT_FILE)],
            cwd=repo_root, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", f"PitchBook results {date.today().isoformat()}"],
            cwd=repo_root, check=True
        )
        subprocess.run(
            ["git", "push", "origin", "HEAD"],
            cwd=repo_root, check=True
        )
        logger.info("Pushed PitchBook results to GitHub")
    except subprocess.CalledProcessError as e:
        logger.error("Git push failed: %s", e)


if __name__ == "__main__":
    main()
