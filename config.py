import os
from dotenv import load_dotenv

load_dotenv()

THESIS = {
    "geographies": {
        "DE": "Germany",
        "AT": "Austria",
        "CH": "Switzerland",
        "GB": "United Kingdom",
        "SE": "Sweden",
        "NO": "Norway",
        "DK": "Denmark",
        "FI": "Finland",
        "NL": "Netherlands",
        "BE": "Belgium",
        "FR": "France",
        "IT": "Italy",
        "ES": "Spain",
        "IL": "Israel",
    },
    "sectors": [
        "Software", "SaaS", "B2B Software", "Enterprise Software",
        "AI", "Artificial Intelligence", "Machine Learning", "Deep Learning",
        "Data", "Data Analytics", "Business Intelligence", "Analytics",
        "Information Services", "Information Technology",
        "Developer Tools", "Infrastructure Software",
        "Fintech", "HR Tech", "Legal Tech", "Proptech",
    ],
    "max_total_funding_eur": 20_000_000,
    "deal_types_include": ["Pre-Seed", "Seed", "Series A"],
    "deal_types_exclude": ["Series B", "Series C", "Series D", "Growth Equity", "Late Stage", "PE Buyout", "IPO"],
    "min_headcount": 25,
    "max_headcount": 300,
    "min_score_threshold": 40,
}

NEWS_FEEDS = [
    {
        "name": "EU-Startups Funding",
        "url": "https://www.eu-startups.com/category/funding-news/feed/",
    },
    {
        "name": "TechCrunch Europe",
        "url": "https://techcrunch.com/category/europe/feed/",
    },
    {
        "name": "TechCrunch Startups",
        "url": "https://techcrunch.com/category/startups/feed/",
    },
]

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-opus-4-7"

PITCHBOOK_API_KEY = os.getenv("PITCHBOOK_API_KEY", "")
PITCHBOOK_BASE_URL = "https://api.pitchbook.com/v1"

GRATA_API_KEY = os.getenv("GRATA_API_KEY", "")
GRATA_BASE_URL = "https://api.grata.com/v1.4"

SALESFORCE_USERNAME = os.getenv("SALESFORCE_USERNAME", "")
SALESFORCE_PASSWORD = os.getenv("SALESFORCE_PASSWORD", "")
SALESFORCE_SECURITY_TOKEN = os.getenv("SALESFORCE_SECURITY_TOKEN", "")
SALESFORCE_DOMAIN = os.getenv("SALESFORCE_DOMAIN", "login")

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
