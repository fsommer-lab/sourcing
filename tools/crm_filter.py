import logging
import re
from pathlib import Path
from typing import Optional

from models import Company

logger = logging.getLogger(__name__)

# Place your Excel file in the sourcing folder with this exact name
PORTFOLIO_FILE = Path(__file__).parent.parent / "portfolio.xlsx"

# Legal suffixes to strip when comparing company names
_SUFFIXES = r"\b(gmbh|ag|ltd|limited|inc|incorporated|bv|nv|sas|srl|sl|oy|ab|as|plc|llc|kg|ohg|se)\b"


def _normalise_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(_SUFFIXES, "", name)
    name = re.sub(r"[^a-z0-9\s]", "", name)
    return name.strip()


def _extract_domain(url: str) -> Optional[str]:
    if not url:
        return None
    url = url.lower().strip().rstrip("/")
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    return url.split("/")[0]  # keep only the domain


def _load_portfolio() -> list[dict]:
    """Load company names and websites from the Excel file."""
    if not PORTFOLIO_FILE.exists():
        logger.warning(
            "No portfolio.xlsx found in sourcing folder — CRM dedup skipped. "
            "Export your pipeline companies to portfolio.xlsx to enable it."
        )
        return []

    try:
        import openpyxl
        wb = openpyxl.load_workbook(PORTFOLIO_FILE, read_only=True, data_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        # Auto-detect header row — find columns named "name" and "website"
        header = [str(c).lower().strip() if c else "" for c in rows[0]]
        name_col = next((i for i, h in enumerate(header) if "name" in h), None)
        web_col = next((i for i, h in enumerate(header) if "web" in h or "url" in h or "domain" in h), None)

        if name_col is None:
            logger.warning("portfolio.xlsx has no 'name' column — skipping dedup")
            return []

        portfolio = []
        for row in rows[1:]:
            name = str(row[name_col]).strip() if row[name_col] else ""
            website = str(row[web_col]).strip() if web_col is not None and row[web_col] else ""
            if name:
                portfolio.append({"name": name, "website": website})

        logger.info("Loaded %d companies from portfolio.xlsx", len(portfolio))
        return portfolio

    except ImportError:
        logger.error("openpyxl not installed — run: pip install openpyxl")
        return []
    except Exception as e:
        logger.error("Failed to read portfolio.xlsx: %s", e)
        return []


def filter_known_companies(companies: list[Company]) -> tuple[list[Company], int]:
    """
    Remove companies that already appear in portfolio.xlsx.
    Returns (new_companies, number_skipped).
    """
    portfolio = _load_portfolio()
    if not portfolio:
        return companies, 0

    known_names = {_normalise_name(p["name"]) for p in portfolio}
    known_domains = {_extract_domain(p["website"]) for p in portfolio if p.get("website")}
    known_domains.discard(None)

    new: list[Company] = []
    skipped = 0

    for company in companies:
        norm_name = _normalise_name(company.name)
        domain = _extract_domain(company.website or "")

        name_match = norm_name in known_names or any(
            norm_name in kn or kn in norm_name
            for kn in known_names
            if len(kn) > 4  # avoid false matches on very short strings
        )
        domain_match = domain and domain in known_domains

        if name_match or domain_match:
            logger.info("Already in portfolio: %s — skipped", company.name)
            skipped += 1
        else:
            new.append(company)

    return new, skipped
