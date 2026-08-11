import re
import pytest

# ── Input validation ───────────────────────────────────────────────────────────
_COMPANY_NAME_RE = re.compile(r"^[a-zA-Z0-9\s\-\.&']{1,60}$")

@pytest.mark.parametrize("name", [
    "Zepto", "Krutrim AI", "Sarvam.AI", "Ola Electric", "PharmEasy",
])
def test_valid_company_names(name):
    assert _COMPANY_NAME_RE.match(name)

@pytest.mark.parametrize("name", [
    "", "a" * 61, "Company; DROP TABLE--", "../../etc/passwd",
])
def test_invalid_company_names(name):
    assert not _COMPANY_NAME_RE.match(name)

# ── Priority scoring ───────────────────────────────────────────────────────────
def _calculate_priority_score(startup: dict) -> int:
    import re
    score = 0
    stage = (startup.get("funding_stage") or "").lower()
    amount_str = (startup.get("funding_amount") or "").replace(",", "").replace("$", "")
    try:
        num = float(re.sub(r"[^\d\.]", "", amount_str.split()[0]))
        if "M" in amount_str.upper() or "million" in amount_str.lower():
            num *= 1_000_000
        elif "B" in amount_str.upper() or "billion" in amount_str.lower():
            num *= 1_000_000_000
    except Exception:
        num = 0

    if any(x in stage for x in ["unicorn", "series d", "series e", "ipo"]):
        score += 40
    elif any(x in stage for x in ["series c"]):
        score += 35
    elif any(x in stage for x in ["series b"]):
        score += 30
    elif any(x in stage for x in ["series a"]):
        score += 25
    elif "pre-series" in stage:
        score += 20
    elif "seed" in stage:
        score += 15
    else:
        score += 5

    if num >= 50_000_000:
        score += 30
    elif num >= 10_000_000:
        score += 20
    elif num >= 1_000_000:
        score += 10
    elif num > 0:
        score += 5

    city = (startup.get("city") or "").lower()
    if any(c in city for c in ["india", "bengaluru", "bangalore", "mumbai", "delhi", "hyderabad", "pune", "chennai"]):
        score += 15

    industry = (startup.get("industry") or "").lower()
    if any(k in industry for k in ["ai", "deeptech", "fintech", "healthtech", "robotics"]):
        score += 10

    return min(score, 100)

def test_unicorn_series_d_gets_top_score():
    s = {"funding_stage": "Series D", "funding_amount": "$100M", "city": "Bengaluru", "industry": "AI"}
    assert _calculate_priority_score(s) == 95

def test_seed_stage_low_score():
    s = {"funding_stage": "Seed", "funding_amount": "$500K", "city": "Bangalore", "industry": "AI"}
    assert _calculate_priority_score(s) == 45

def test_unknown_stage_minimum_score():
    s = {"funding_stage": "Bootstrapped", "funding_amount": "Undisclosed", "city": "Unknown", "industry": "Unknown"}
    assert _calculate_priority_score(s) == 5

def test_score_capped_at_100():
    s = {"funding_stage": "Unicorn", "funding_amount": "$500M", "city": "Mumbai", "industry": "AI"}
    assert _calculate_priority_score(s) == 95
