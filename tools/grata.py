import logging
from typing import Optional

import requests

from config import GRATA_API_KEY, GRATA_BASE_URL, THESIS
from models import Company

logger = logging.getLogger(__name__)


class GrataClient:
    """
    Wraps the Grata Search API.
    API key is available in your Grata account under Settings → API Access.
    If not set the client is silently skipped.
    """

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {GRATA_API_KEY}",
            "Content-Type": "application/json",
        })

    @property
    def _configured(self) -> bool:
        return bool(GRATA_API_KEY)

    def search_companies(self) -> list[Company]:
        if not self._configured:
            logger.warning("Grata API key not set — skipping Grata")
            return []

        country_names = list(THESIS["geographies"].values())

        payload = {
            "terms": ["software", "SaaS", "AI", "data analytics", "enterprise software"],
            "terms_filter": "any",
            "headquartersLocations": [{"country": c} for c in country_names],
            "employeesRange": {
                "min": THESIS["min_headcount"],
                "max": THESIS["max_headcount"],
            },
            "fundingFilter": {
                "fundingTypes": ["Seed", "Series A", "Bootstrapped"],
                "maxTotalFunding": THESIS["max_total_funding_eur"],
            },
            "sortBy": "employee_growth",
            "sortOrder": "desc",
            "pageSize": 50,
        }

        try:
            resp = self._session.post(
                f"{GRATA_BASE_URL}/search", json=payload, timeout=30
            )
            resp.raise_for_status()
            items = resp.json().get("companies", [])
            companies = [c for c in (self._parse(item) for item in items) if c]
            logger.info("Grata: %d companies found", len(companies))
            return companies

        except requests.HTTPError as e:
            logger.error(
                "Grata HTTP %s: %s", e.response.status_code, e.response.text[:200]
            )
        except Exception as e:
            logger.error("Grata request failed: %s", e)

        return []

    def _parse(self, item: dict) -> Optional[Company]:
        try:
            latest_round = item.get("latestFundingRound") or {}
            return Company(
                name=item.get("name", "Unknown"),
                country=item.get("country", "Unknown"),
                sector=item.get("industry", "Software"),
                description=item.get("description", ""),
                total_funding_eur=_to_float(item.get("totalFunding")),
                last_round_type=latest_round.get("type"),
                last_round_amount_eur=_to_float(latest_round.get("amount")),
                last_round_date=latest_round.get("date"),
                headcount=item.get("employeeCount"),
                founded_year=item.get("foundedYear"),
                website=item.get("domain"),
                source="grata",
                source_url=(
                    f"https://grata.com/profile/{item['id']}"
                    if item.get("id")
                    else None
                ),
            )
        except Exception as e:
            logger.warning("Failed to parse Grata company: %s", e)
            return None


def _to_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
