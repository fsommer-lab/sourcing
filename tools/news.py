import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from config import NEWS_FEEDS
from models import Company
from tools.claude_cli import call_claude, parse_json

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
Read this news article and extract information about a European tech startup that raised funding.

Article title: {title}
Article text: {text}

Only extract a company if ALL of these apply:
- Based in Europe or Israel
- Sector is Software, SaaS, AI/ML, Data, Analytics, or B2B tech
- Round is Pre-Seed, Seed, or Series A only (ignore Series B+)
- Total funding raised is €20M or less

Return ONLY a JSON object, no markdown, no explanation:
{{
  "relevant": true or false,
  "company_name": "string or null",
  "country": "full country name or null",
  "sector": "e.g. SaaS, AI, Data Analytics or null",
  "description": "2-3 sentences on what they do or null",
  "funding_amount_eur": number in euros or null,
  "round_type": "Pre-Seed | Seed | Series A | null",
  "total_funding_eur": number in euros or null,
  "headcount": number or null,
  "founded_year": number or null,
  "website": "URL or null"
}}

Currency conversion if needed: 1 USD ≈ 0.92 EUR, 1 GBP ≈ 1.17 EUR.
If not relevant return {{"relevant": false}}.\
"""


def _fetch_articles(hours_back: int = 24) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    articles = []

    for feed_cfg in NEWS_FEEDS:
        try:
            feed = feedparser.parse(feed_cfg["url"])
            for entry in feed.entries:
                pub = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if pub and pub < cutoff:
                    continue
                articles.append({
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "summary": entry.get("summary", ""),
                    "published": pub.isoformat() if pub else None,
                    "source": feed_cfg["name"],
                })
        except Exception as e:
            logger.warning("Failed to fetch feed %s: %s", feed_cfg["name"], e)

    logger.info("Fetched %d articles from RSS feeds", len(articles))
    return articles


def _fetch_article_text(url: str) -> str:
    try:
        resp = requests.get(
            url, timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SourcingBot/1.0)"},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["nav", "footer", "aside", "script", "style", "form"]):
            tag.decompose()
        content = (
            soup.find("article")
            or soup.find("main")
            or soup.find(class_=["article-body", "post-content", "entry-content"])
            or soup.find("body")
        )
        text = content.get_text(separator=" ", strip=True) if content else ""
        return text[:4000]
    except Exception as e:
        logger.warning("Failed to fetch article %s: %s", url, e)
        return ""


def _extract_company(article: dict) -> Optional[Company]:
    text = _fetch_article_text(article["url"]) or article.get("summary", "")
    if not text:
        return None

    prompt = _EXTRACTION_PROMPT.format(title=article["title"], text=text)
    output = call_claude(prompt, timeout=60)
    if not output:
        return None

    data = parse_json(output)
    if not isinstance(data, dict) or not data.get("relevant"):
        return None

    return Company(
        name=data.get("company_name") or "Unknown",
        country=data.get("country") or "Unknown",
        sector=data.get("sector") or "Software",
        description=data.get("description") or "",
        total_funding_eur=data.get("total_funding_eur"),
        last_round_type=data.get("round_type"),
        last_round_amount_eur=data.get("funding_amount_eur"),
        headcount=data.get("headcount"),
        founded_year=data.get("founded_year"),
        website=data.get("website"),
        source=article["source"],
        source_url=article["url"],
        article_title=article["title"],
    )


def run_news_agent(hours_back: int = 24) -> list[Company]:
    articles = _fetch_articles(hours_back=hours_back)
    companies: list[Company] = []
    for article in articles:
        company = _extract_company(article)
        if company:
            logger.info("News match: %s (%s) via %s", company.name, company.country, article["source"])
            companies.append(company)
    return companies
