#!/usr/bin/env python3
"""
VC Portfolio Sourcing Tool for Spectrum Equity.

Looks up a VC fund's portfolio on PitchBook and surfaces companies that
match Spectrum's growth-equity thesis (stage, funding, business model).

Usage:
    python3 vc_portfolio_sourcing.py "Bessemer Venture Partners"
    python3 vc_portfolio_sourcing.py "Insight Partners" --min-score 30
    python3 vc_portfolio_sourcing.py "Sequoia Capital" --json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools.claude_cli import call_claude, parse_json

# ── Spectrum Equity investment thesis ─────────────────────────────────────────

SPECTRUM_THESIS = {
    # Spectrum is a growth equity firm — they want companies already scaling
    "target_stages": [
        "Series B", "Series C", "Series D", "Series E",
        "Growth Equity", "Late Stage VC", "Growth",
    ],
    "early_stages": [
        "Pre-Seed", "Seed", "Series A",
    ],
    # Total capital raised range (USD) — too small means pre-scale,
    # too large means already institutional PE territory
    "total_funding_min_usd": 15_000_000,
    "total_funding_max_usd": 600_000_000,
    # Business models Spectrum targets
    "business_model_keywords": [
        "saas", "software", "subscription", "platform", "marketplace",
        "internet", "digital media", "information services", "data",
        "e-commerce", "fintech", "edtech", "healthtech",
    ],
    # Sectors they like
    "target_sectors": [
        "Software", "SaaS", "B2B Software", "Enterprise Software",
        "Internet", "Marketplace", "E-Commerce",
        "Fintech", "Financial Technology", "Insurtech",
        "EdTech", "Education Technology",
        "Health Tech", "Health Information",
        "Marketing Tech", "AdTech",
        "HR Tech", "Legal Tech", "PropTech",
        "Data & Analytics", "Developer Tools",
        "Digital Media", "Information Services",
    ],
    # Hard excludes — not software/internet businesses
    "excluded_sectors": [
        "Hardware", "Semiconductors", "Biotech", "Pharma",
        "Cleantech", "Energy", "Real Estate", "Construction",
        "Food & Beverage", "Retail", "Manufacturing",
    ],
}

# ── PitchBook prompt ───────────────────────────────────────────────────────────

_PORTFOLIO_PROMPT = """\
Use PitchBook to fetch the full portfolio of the VC investor "{fund_name}".

Steps:
1. Call pitchbook_search with query="{fund_name}" to find the investor record.
2. Call pitchbook_get_investor_investments with the investor's PBID to list all portfolio companies.
3. For each portfolio company, record the deal stage, total funding, and sector.

Return ONLY a JSON array with no markdown or extra text:
[{{
  "name": "company name",
  "sector": "primary sector as listed on PitchBook",
  "description": "1-2 sentences: what the company does and how it makes money",
  "business_model": "SaaS | software | marketplace | services | media | hardware | biotech | other",
  "hq_country": "country name or null",
  "hq_city": "city name or null",
  "founded_year": number or null,
  "total_funding_usd": total USD raised across all rounds as a number or null,
  "last_round_type": "exact round type string from PitchBook or null",
  "last_round_amount_usd": number or null,
  "last_round_date": "YYYY-MM-DD or null",
  "website": "URL or null",
  "pitchbook_url": "PitchBook URL or null"
}}]

If the investor is not found, return: []
If the investor has no recorded investments, return: []
"""


# ── Scoring logic ──────────────────────────────────────────────────────────────

def score_company(c: dict) -> tuple[int, list[str]]:
    """Score 0-100 on how well a company fits Spectrum's thesis."""
    score = 0
    reasons: list[str] = []
    penalties: list[str] = []

    round_type = (c.get("last_round_type") or "").strip()
    sector = (c.get("sector") or "").lower()
    bm = (c.get("business_model") or "").lower()
    desc = (c.get("description") or "").lower()
    total = _to_float(c.get("total_funding_usd"))

    # ── Stage (40 pts) ────────────────────────────────────────────────────────
    rt_lower = round_type.lower()
    if any(s.lower() in rt_lower for s in SPECTRUM_THESIS["target_stages"]):
        score += 40
        reasons.append(f"Stage match ({round_type})")
    elif any(s.lower() in rt_lower for s in SPECTRUM_THESIS["early_stages"]):
        score -= 10
        penalties.append(f"Too early ({round_type})")

    # ── Business model (30 pts) ───────────────────────────────────────────────
    text = f"{bm} {desc} {sector}"
    bm_hits = [kw for kw in SPECTRUM_THESIS["business_model_keywords"] if kw in text]
    if bm_hits:
        score += 30
        reasons.append(f"Software/internet model ({bm or bm_hits[0]})")

    # ── Sector fit (15 pts) ───────────────────────────────────────────────────
    excl = [s.lower() for s in SPECTRUM_THESIS["excluded_sectors"]]
    incl = [s.lower() for s in SPECTRUM_THESIS["target_sectors"]]
    if any(kw in sector for kw in excl):
        score -= 30
        penalties.append(f"Excluded sector ({c.get('sector')})")
    elif any(kw in sector for kw in incl):
        score += 15
        reasons.append(f"Target sector ({c.get('sector')})")

    # ── Funding range (15 pts) ────────────────────────────────────────────────
    fmin = SPECTRUM_THESIS["total_funding_min_usd"]
    fmax = SPECTRUM_THESIS["total_funding_max_usd"]
    if total is not None:
        if fmin <= total <= fmax:
            score += 15
            reasons.append(f"Funding in range (${total/1e6:.0f}M total raised)")
        elif total > fmax:
            penalties.append(f"Likely over-capitalised (${total/1e6:.0f}M total raised)")
        else:
            penalties.append(f"Possibly pre-scale (${total/1e6:.0f}M total raised)")

    return max(score, 0), reasons + (["⚠ " + p for p in penalties] if penalties else [])


# ── PitchBook fetch ────────────────────────────────────────────────────────────

def fetch_portfolio(fund_name: str) -> list[dict]:
    prompt = _PORTFOLIO_PROMPT.format(fund_name=fund_name)
    print(f"Querying PitchBook for '{fund_name}' portfolio…", flush=True)
    output = call_claude(prompt, timeout=300)
    if not output:
        print("ERROR: No response from PitchBook.", file=sys.stderr)
        return []
    data = parse_json(output)
    if not isinstance(data, list):
        print(f"ERROR: Unexpected response:\n{output[:500]}", file=sys.stderr)
        return []
    return data


# ── Report formatting ──────────────────────────────────────────────────────────

def format_report(fund_name: str, companies: list[dict], min_score: int) -> str:
    if not companies:
        return f"No portfolio companies found for '{fund_name}'."

    scored = sorted(
        [(score_company(c), c) for c in companies],
        key=lambda x: x[0][0],
        reverse=True,
    )

    relevant = [(sc, c) for (sc, c) in scored if sc[0] >= min_score]
    skipped  = [(sc, c) for (sc, c) in scored if sc[0] < min_score]

    lines = [
        "",
        f"  Spectrum Equity — VC Portfolio Sourcing Report",
        f"  Fund: {fund_name}",
        "  " + "─" * 58,
        f"  Portfolio companies found : {len(companies)}",
        f"  Relevant (score ≥ {min_score})    : {len(relevant)}",
        "",
    ]

    if relevant:
        lines += ["  RELEVANT COMPANIES", "  " + "─" * 58]
        for (score, reasons), c in relevant:
            last_round = _fmt_round(c)
            lines += [
                "",
                f"  {c['name']}   [score {score}/100]",
                f"  {c.get('description') or 'No description available.'}",
                f"  Sector: {c.get('sector') or 'N/A'}  |  Model: {c.get('business_model') or 'N/A'}",
                f"  {last_round}",
            ]
            for r in reasons:
                lines.append(f"    • {r}")
            if c.get("website"):
                lines.append(f"  {c['website']}")
            if c.get("pitchbook_url"):
                lines.append(f"  PB: {c['pitchbook_url']}")

    if skipped:
        lines += ["", "  BELOW THRESHOLD", "  " + "─" * 58]
        for (score, _), c in skipped:
            lines.append(
                f"  {c['name']}   [score {score}]  —  "
                f"{c.get('sector') or 'N/A'}, {c.get('last_round_type') or 'N/A'}"
            )

    lines.append("")
    return "\n".join(lines)


def _fmt_round(c: dict) -> str:
    parts = []
    if c.get("last_round_type"):
        parts.append(f"Stage: {c['last_round_type']}")
    if c.get("last_round_amount_usd"):
        parts.append(f"Last round: ${_to_float(c['last_round_amount_usd'])/1e6:.0f}M")
    if c.get("total_funding_usd"):
        parts.append(f"Total raised: ${_to_float(c['total_funding_usd'])/1e6:.0f}M")
    if c.get("last_round_date"):
        parts.append(f"Date: {c['last_round_date']}")
    return "  " + "  |  ".join(parts) if parts else "  No deal data"


def _to_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spectrum Equity VC portfolio sourcing — powered by PitchBook"
    )
    parser.add_argument("fund_name", help="VC fund name, e.g. 'Bessemer Venture Partners'")
    parser.add_argument(
        "--min-score", type=int, default=40,
        help="Minimum relevance score to appear in the 'Relevant' section (default: 40)"
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Output raw scored JSON instead of the formatted report"
    )
    args = parser.parse_args()

    companies = fetch_portfolio(args.fund_name)

    if args.as_json:
        output = []
        for c in companies:
            score, reasons = score_company(c)
            output.append({**c, "spectrum_score": score, "spectrum_reasons": reasons})
        output.sort(key=lambda x: x["spectrum_score"], reverse=True)
        print(json.dumps(output, indent=2))
        return

    report = format_report(args.fund_name, companies, args.min_score)
    print(report)


if __name__ == "__main__":
    main()
