import os
from dotenv import load_dotenv

load_dotenv()

APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")
DRY_RUN = os.getenv("DRY_RUN", "True").lower() in ("true", "1", "yes")

# Google Sheets Config
GOOGLE_SHEETS_CREDENTIALS = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "credentials.json")
GOOGLE_SHEETS_ID = os.getenv("SHEET_ID", "")
GOOGLE_SHEET_WORKSHEET = os.getenv("GOOGLE_SHEET_WORKSHEET", "LinkedIn Hiring Leads")

# ── Role Keywords (Phase 1) ───────────────────────────────────────────────────
FOUNDERS_OFFICE_KEYWORDS = [
    "founder's office",
    "founders office",
    "founder\u2019s office",
    "founder office",
    "office of the founder",
    "founder associate",
    "founder's associate",
    "founder\u2019s associate",
]

CHIEF_OF_STAFF_KEYWORDS = [
    "chief of staff",
]

# FO is ambiguous — only accepted when an explicit Founder's Office variant
# appears nearby in the same post. Generic words (founder, startup, CEO)
# are NOT sufficient.
FO_AMBIGUOUS_KEYWORD = "fo"
FO_PROOF_PHRASES = [
    "founder's office", "founders office", "founder\u2019s office",
    "founder office", "office of the founder",
    "founder associate", "founder's associate", "founder\u2019s associate",
]

# ── Hiring Signals ────────────────────────────────────────────────────────────
HIRING_SIGNALS = [
    "hiring", "we're hiring", "we are hiring", "now hiring",
    "hiring for", "looking for", "we're looking for", "we are looking for",
    "looking to hire", "opening", "open role", "open position",
    "job opening", "join our team", "building our team",
    "team is hiring", "apply", "applications open",
    "send resume", "send your resume", "send cv", "send your cv",
    "dm to apply", "referrals", "referrals welcome",
    "seeking candidates",
]

# ── Exclusions ────────────────────────────────────────────────────────────────
EXCLUSIONS_JOB_SEEKER = [
    "i am looking for", "i'm looking for", "i\u2019m looking for",
    "open to work", "hire me", "seeking opportunities",
    "seeking a role", "seeking founder's office",
    "open to opportunities", "actively looking",
]

EXCLUSIONS_CONGRATULATORY = [
    "excited to join", "thrilled to announce", "i have joined",
    "i've joined", "i\u2019ve joined", "congratulations",
    "my new role", "starting my new position",
    "happy to share that i", "delighted to announce",
]

EXCLUSIONS_INFORMATIONAL = [
    "things i learned as", "what does a", "salary discussion",
    "salaries can reach", "role is important for",
    "lessons from being", "my experience as",
]

INTERNSHIP_INDICATORS = [
    "intern", "internship", "internship opportunity",
    "summer intern", "student intern", "campus hiring",
    "graduate internship",
]

# ── India Location Signals ────────────────────────────────────────────────────
INDIA_CITIES = [
    "bangalore", "bengaluru", "mumbai", "delhi", "delhi ncr",
    "gurgaon", "gurugram", "noida", "hyderabad", "pune",
    "chennai", "kolkata", "ahmedabad", "jaipur", "india",
    "remote - india", "remote india",
]

# ── Non-India Locations (for absolute rejection) ─────────────────────────────
NON_INDIA_LOCATIONS = [
    "london", "new york", "dubai", "singapore", "toronto",
    "san francisco", "seattle", "boston", "chicago", "los angeles",
    "berlin", "paris", "amsterdam", "tokyo", "sydney", "melbourne",
]

NON_INDIA_COUNTRIES = [
    "united states", "united kingdom", "canada", "australia",
    "germany", "france", "netherlands", "japan",
]

# ── Compensation Signals (Indian Context Only) ────────────────────────────────
INDIA_COMPENSATION = ["lpa", "₹", "inr", "ctc", "lakhs"]

# ── Confidence ────────────────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.70"))

# ── Proximity Rule ────────────────────────────────────────────────────────────
# Role keyword and hiring signal must appear within this many characters.
PROXIMITY_CHAR_LIMIT = int(os.getenv("HIRING_PROXIMITY_CHARS", "300"))

# ── Search Queries (targeted, high-precision) ─────────────────────────────────
# Do NOT search for generic "remote" without India explicit context.
SEARCH_QUERIES = [
    '"founder\'s office" hiring',
    '"founders office" hiring',
    '"founder office" hiring',
    '"chief of staff" hiring',
    '"founder\'s office" "LPA"',
    '"chief of staff" "LPA"',
    '"founder\'s office" apply',
    '"chief of staff" apply',
    '"founder associate" hiring',
    '"founder\'s associate" hiring',
    '"office of the founder" hiring',
    '"chief of staff" India',
    '"founder\'s office" India',
    '"founder\'s office" Bangalore',
    '"founder\'s office" Mumbai',
    '"chief of staff" Bangalore',
    '"chief of staff" Mumbai',
    '"chief of staff" "Remote India"',
    '"founder\'s office" "Remote India"',
]
