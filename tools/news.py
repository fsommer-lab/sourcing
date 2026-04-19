import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import atoma
import requests
from bs4 import BeautifulSoup

from config import NEWS_FEEDS
from models import Company

logger = logging.getLogger(__name__)

# ── Amount ─────────────────────────────────────────────────────────────────────
_AMOUNT_RE = re.compile(r'([€$£])\s*(\d+(?:[.,]\d+)?)\s*([MmKk])\b')

# ── Round type ─────────────────────────────────────────────────────────────────
_ROUND_PATTERNS = [
    ("Pre-Seed", re.compile(r'\bpre[-\s]?seed\b', re.I)),
    ("Seed",     re.compile(r'\bseed\b(?!\s*(?:fund|investor|firm|stage|capital))', re.I)),
    ("Series A", re.compile(r'\bseries[\s-]?a\b', re.I)),
    ("Series B", re.compile(r'\bseries[\s-]?b\b', re.I)),
    ("Series C", re.compile(r'\bseries[\s-]?c\b', re.I)),
]
_THESIS_ROUNDS = {"Pre-Seed", "Seed", "Series A"}

_FUNDING_VERB = re.compile(
    r'\b(?:raises?|raised|secures?|secured|lands?|landed|closes?|closed|'
    r'announces?|announced|gets?|got|bags?|bagged|attracts?|attracted|'
    r'completes?|completed)\b',
    re.I,
)

# ── Geography ──────────────────────────────────────────────────────────────────
_CITY_COUNTRY: list[tuple[str, list[str]]] = [
    ("Germany",        ["berlin", "munich", "münchen", "hamburg", "frankfurt", "cologne", "köln",
                        "düsseldorf", "dusseldorf", "german", "germany"]),
    ("Austria",        ["vienna", "wien", "graz", "salzburg", "austrian", "austria"]),
    ("Switzerland",    ["zurich", "zürich", "geneva", "genève", "basel", "bern", "swiss", "switzerland"]),
    ("United Kingdom", ["london", "manchester", "edinburgh", "cambridge", "oxford", "bristol",
                        "glasgow", "uk-based", "british", " uk,", "(uk)", "united kingdom"]),
    ("Sweden",         ["stockholm", "gothenburg", "göteborg", "malmö", "malmoe", "swedish", "sweden"]),
    ("Norway",         ["oslo", "bergen", "trondheim", "norwegian", "norway"]),
    ("Denmark",        ["copenhagen", "københavn", "aarhus", "danish", "denmark"]),
    ("Finland",        ["helsinki", "tampere", "finnish", "finland"]),
    ("Netherlands",    ["amsterdam", "rotterdam", "eindhoven", "utrecht", "dutch", "netherlands"]),
    ("Belgium",        ["brussels", "bruxelles", "ghent", "antwerp", "belgian", "belgium"]),
    ("France",         ["paris", "lyon", "bordeaux", "marseille", "toulouse", "french", "france"]),
    ("Italy",          ["milan", "milano", "rome", "roma", "turin", "torino", "italian", "italy"]),
    ("Spain",          ["madrid", "barcelona", "seville", "sevilla", "spanish", "spain"]),
    ("Israel",         ["tel aviv", "tel-aviv", "haifa", "jerusalem", "israeli", "israel"]),
]

# ── Sector ─────────────────────────────────────────────────────────────────────
_SECTOR_KEYWORDS: list[tuple[str, list[str]]] = [
    ("AI",              ["artificial intelligence", " ai ", "machine learning", "deep learning",
                         "llm", "generative ai", "nlp", "computer vision"]),
    ("Cybersecurity",   ["cybersecurity", "cyber security", "infosec", "zero trust"]),
    ("Fintech",         ["fintech", "financial technology", "payments", "insurtech",
                         "regtech", "lending", "wealthtech", "neobank"]),
    ("HR Tech",         ["hr tech", "hrtech", "human resources", "workforce",
                         "recruitment", "talent management", "payroll"]),
    ("Data Analytics",  ["data analytics", "business intelligence", "analytics platform",
                         "data platform"]),
    ("Developer Tools", ["developer tools", "devops", "developer platform", "devsecops"]),
    ("SaaS",            ["saas", "b2b saas", "cloud software"]),
    ("Software",        ["software", "platform", "b2b", "enterprise"]),
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_amount(text: str) -> Optional[float]:
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    symbol, num_str, suffix = m.group(1), m.group(2).replace(",", "."), m.group(3).upper()
    amount = float(num_str) * (1_000_000 if suffix == "M" else 1_000)
    if symbol == "$":
        amount *= 0.92
    elif symbol == "£":
        amount *= 1.17
    return amount


def _parse_round(text: str) -> Optional[str]:
    for name, pattern in _ROUND_PATTERNS:
        if pattern.search(text):
            return name
    return None


def _parse_country(text: str) -> str:
    lower = text.lower()
    for country, keywords in _CITY_COUNTRY:
        if any(kw in lower for kw in keywords):
            return country
    return "Unknown"


def _parse_sector(text: str) -> str:
    lower = text.lower()
    for sector, keywords in _SECTOR_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return sector
    return "Software"


def _parse_name_from_title(title: str) -> Optional[str]:
    """Extract company name from 'Company raises €X ...' style titles."""
    m = re.match(r'^([\w][^\n,]{2,60}?)\s+' + _FUNDING_VERB.pattern, title, re.I)
    if not m:
        return None
    name = m.group(1).strip()
    # Strip trailing descriptors: "a Berlin-based startup", "the German..."
    name = re.sub(r',?\s+(?:a|the|an)\s+\w.*$', '', name, flags=re.I).strip()
    # Strip trailing legal suffixes
    name = re.sub(r'\s+(?:gmbh|ag|ltd|inc|bv|sas|srl|ab|oy|plc)\s*$', '', name, flags=re.I).strip()
    return name if len(name) > 2 else None


def _fetch_article_text(url: str) -> str:
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["nav", "footer", "aside", "script", "style", "form"]):
            tag.decompose()
        content = (soup.find("article") or soup.find("main")
                   or soup.find(class_=["article-body", "post-content", "entry-content"])
                   or soup.find("body"))
        return (content.get_text(separator=" ", strip=True) if content else "")[:4000]
    except Exception as e:
        logger.warning("Failed to fetch article %s: %s", url, e)
        return ""


# ── Extraction strategies ──────────────────────────────────────────────────────

def _extract_via_claude(article: dict) -> Optional[Company]:
    """NLP extraction using Claude — only when ANTHROPIC_API_KEY is set."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        from tools.claude_cli import call_claude, parse_json

        _PROMPT = """\
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

Currency conversion: 1 USD = 0.92 EUR, 1 GBP = 1.17 EUR.
If not relevant return {{"relevant": false}}.\
"""
        text = _fetch_article_text(article["url"]) or article.get("summary", "")
        if not text:
            return None
        output = call_claude(_PROMPT.format(title=article["title"], text=text[:3000]), timeout=60)
        data = parse_json(output) if output else None
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
    except Exception as e:
        logger.warning("Claude extraction failed for %s: %s", article.get("url"), e)
        return None


def _extract_via_regex(article: dict) -> Optional[Company]:
    """Rule-based extraction — no external API needed."""
    title = article["title"]
    summary = article.get("summary") or ""
    full = title + " " + summary

    amount = _parse_amount(full)
    round_type = _parse_round(full)

    # Must look like a funding article
    if not amount and not round_type:
        return None

    # Skip Series B+
    if round_type and round_type not in _THESIS_ROUNDS:
        return None

    name = _parse_name_from_title(title)
    if not name:
        return None

    return Company(
        name=name,
        country=_parse_country(full),
        sector=_parse_sector(full),
        description="",
        total_funding_eur=amount,
        last_round_type=round_type,
        last_round_amount_eur=amount,
        source=article["source"],
        source_url=article["url"],
        article_title=title,
    )


def _fetch_articles(hours_back: int = 24) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    articles = []

    for feed_cfg in NEWS_FEEDS:
        try:
            resp = requests.get(feed_cfg["url"], timeout=10,
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            feed = atoma.parse_rss_bytes(resp.content)

            for entry in feed.items:
                pub = entry.pub_date
                if pub and pub.replace(tzinfo=timezone.utc) < cutoff:
                    continue
                articles.append({
                    "title": entry.title or "",
                    "url": entry.link or "",
                    "summary": entry.description or "",
                    "source": feed_cfg["name"],
                })
        except Exception as e:
            logger.warning("Feed error %s: %s", feed_cfg["name"], e)

    logger.info("Fetched %d articles from RSS feeds", len(articles))
    return articles


def run_news_agent(hours_back: int = 24) -> list[Company]:
    articles = _fetch_articles(hours_back=hours_back)
    companies: list[Company] = []
    for article in articles:
        # Try Claude NLP first (if API key set), fall back to regex
        company = _extract_via_claude(article) or _extract_via_regex(article)
        if company:
            logger.info("News match: %s (%s) via %s",
                        company.name, company.country, article["source"])
            companies.append(company)
    return companies
