from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Company:
    name: str
    country: str
    sector: str
    description: str

    total_funding_eur: Optional[float] = None
    last_round_type: Optional[str] = None
    last_round_amount_eur: Optional[float] = None
    last_round_date: Optional[str] = None

    headcount: Optional[int] = None
    founded_year: Optional[int] = None

    website: Optional[str] = None
    pitchbook_url: Optional[str] = None

    source: str = ""
    source_url: Optional[str] = None
    article_title: Optional[str] = None

    score: Optional[float] = None
    score_breakdown: dict = field(default_factory=dict)

    in_salesforce: bool = False
    salesforce_id: Optional[str] = None
