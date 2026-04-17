import logging
from datetime import datetime, timedelta
from typing import Optional

import requests

from config import PITCHBOOK_API_KEY, PITCHBOOK_BASE_URL, THESIS
from models import Company

logger = logging.getLogger(__name__)

_GEO_MAP = THESIS["geographies"]  # code -> name


class PitchBookClient:
    """
    Wraps the PitchBook Data API.

    NOTE: Data API access is a separate license from the PitchBook web platform.
    Contact your PitchBook account rep to enable it and get an API key.
    If PITCHBOOK_API_KEY is not set the client is silently skipped.
    """

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {PITCHBOOK_API_KEY}",
            "Content-Type": "application/json",
        })

    @property
    def _configured(self) -> bool:
        return bool(PITCHBOOK_API_KEY)

    def search_recent_deals(self, days_back: int = 1) -> list[Company]:
        if not self._configured:
            logger.warning("PitchBook API key not set — skipping PitchBook")
            return []

        since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        params = {
            "dealTypes": "Pre-Seed,Seed,Series A",
            "dealDateFrom": since,
            "countries": ",".join(_GEO_MAP.keys()),
            "industries": (
                "Software,SaaS,Artificial Intelligence,"
                "Data & Analytics,Information Technology"
            ),
            "dealSizeCurrencyCode": "EUR",
            "dealSizeMax": THESIS["max_total_funding_eur"],
            "limit": 100,
        }

        try:
            resp = self._session.get(
                f"{PITCHBOOK_BASE_URL}/deals", params=params, timeout=30
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            companies = [c for c in (self._parse_deal(d) for d in results) if c]
            logger.info("PitchBook: %d deals found", len(companies))
            return companies

        except requests.HTTPError as e:
            logger.error(
                "PitchBook HTTP %s: %s", e.response.status_code, e.response.text[:200]
            )
        except Exception as e:
            logger.error("PitchBook request failed: %s", e)

        return []

    def _parse_deal(self, deal: dict) -> Optional[Company]:
        try:
            co = deal.get("company", {})
            country_code = co.get("countryCode", "")
            country = _GEO_MAP.get(country_code, country_code)

            return Company(
                name=co.get("name", "Unknown"),
                country=country,
                sector=co.get("primaryIndustry", "Software"),
                description=co.get("description", ""),
                total_funding_eur=_to_float(co.get("totalFundingEur") or co.get("totalFunding")),
                last_round_type=deal.get("dealType"),
                last_round_amount_eur=_to_float(deal.get("dealSizeEur") or deal.get("dealSize")),
                last_round_date=deal.get("dealDate"),
                headcount=co.get("employeeCount"),
                founded_year=co.get("foundedYear"),
                website=co.get("websiteUrl"),
                pitchbook_url=co.get("profileUrl"),
                source="pitchbook",
                source_url=co.get("profileUrl"),
            )
        except Exception as e:
            logger.warning("Failed to parse PitchBook deal: %s", e)
            return None


def _to_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
