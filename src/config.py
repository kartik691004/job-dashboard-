import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# -- Gemini AI -----------------------------------------------------------------
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# -- Apify ---------------------------------------------------------------------
APIFY_API_TOKEN  = os.getenv("APIFY_API_TOKEN", "")

# -- Output Paths --------------------------------------------------------------
EXCEL_OUTPUT_PATH = os.getenv("EXCEL_OUTPUT_PATH", str(BASE_DIR / "excel" / "Indian_Startup_Outreach.xlsx"))
LOG_FILE_PATH     = os.getenv("LOG_FILE_PATH",   str(BASE_DIR / "logs"  / "pipeline_execution.log"))
DB_PATH           = str(BASE_DIR / "data" / "ftb_store.db")

# -- Google Sheets -------------------------------------------------------------
GOOGLE_SHEETS_ENABLED     = os.getenv("GOOGLE_SHEETS_ENABLED", "false").lower() in ("true", "1", "yes")
GOOGLE_SHEETS_CREDENTIALS = os.getenv("GOOGLE_SHEETS_CREDENTIALS", str(BASE_DIR / "credentials.json"))
GOOGLE_SHEETS_ID          = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_SHEETS_NAME        = os.getenv("GOOGLE_SHEETS_NAME", "Indian Startup Outreach")

# Ensure dirs exist
for d in [BASE_DIR / "excel", BASE_DIR / "logs", BASE_DIR / "data"]:
    d.mkdir(exist_ok=True)

# -- Scheduler -----------------------------------------------------------------
SCHEDULER_TIMEZONE    = os.getenv("SCHEDULER_TIMEZONE",    "Asia/Kolkata")
SCHEDULER_DAY_OF_WEEK = os.getenv("SCHEDULER_DAY_OF_WEEK", "*")
SCHEDULER_HOUR        = int(os.getenv("SCHEDULER_HOUR",    "1"))
SCHEDULER_MINUTE      = int(os.getenv("SCHEDULER_MINUTE",  "0"))

# -- Pipeline ------------------------------------------------------------------
PIPELINE_STARTUP_LIMIT    = int(os.getenv("PIPELINE_STARTUP_LIMIT", "50"))
PIPELINE_REFRESH_EXISTING = os.getenv("PIPELINE_REFRESH_EXISTING", "true").lower() in ("true", "1", "yes")

# -- Early-stage Filtering -----------------------------------------------------
PIPELINE_FOUNDING_YEAR_MIN = int(os.getenv("PIPELINE_FOUNDING_YEAR_MIN", "2020"))
PIPELINE_EMPLOYEE_MAX      = int(os.getenv("PIPELINE_EMPLOYEE_MAX", "100"))
PIPELINE_TARGET_STAGES     = [
    s.strip() for s in
    os.getenv("PIPELINE_TARGET_STAGES", "Pre-Seed,Seed,Angel,Bootstrap,Series A,Recently Funded").split(",")
]

# -- Source Enable/Disable Flags -----------------------------------------------
SOURCE_STARTUP_INDIA   = os.getenv("SOURCE_STARTUP_INDIA",   "true").lower() in ("true", "1", "yes")
SOURCE_NEWS_SITES      = os.getenv("SOURCE_NEWS_SITES",      "true").lower() in ("true", "1", "yes")
SOURCE_PRODUCT_HUNT    = os.getenv("SOURCE_PRODUCT_HUNT",    "true").lower() in ("true", "1", "yes")
SOURCE_WELLFOUND       = os.getenv("SOURCE_WELLFOUND",       "true").lower() in ("true", "1", "yes")
SOURCE_VC_PORTFOLIOS   = os.getenv("SOURCE_VC_PORTFOLIOS",   "true").lower() in ("true", "1", "yes")
SOURCE_TECH_NEWS       = os.getenv("SOURCE_TECH_NEWS",       "true").lower() in ("true", "1", "yes")
SOURCE_LINKEDIN_JOBS   = os.getenv("SOURCE_LINKEDIN_JOBS",   "true").lower() in ("true", "1", "yes")
SOURCE_JOB_PLATFORMS   = os.getenv("SOURCE_JOB_PLATFORMS",   "true").lower() in ("true", "1", "yes")
SOURCE_Y_COMBINATOR    = os.getenv("SOURCE_Y_COMBINATOR",    "true").lower() in ("true", "1", "yes")
SOURCE_FACEBOOK_ADS    = os.getenv("SOURCE_FACEBOOK_ADS",    "true").lower() in ("true", "1", "yes")

# -- Facebook Ads Discovery Config -------------------------------------------
FB_ADS_SEARCH_TERMS = [
    t.strip() for t in os.getenv("FB_ADS_SEARCH_TERMS", "startup,funded,series a,seed funding,fintech,healthtech,edtech").split(",")
]
FB_ADS_COUNTRY = os.getenv("FB_ADS_COUNTRY", "IN")
FB_ADS_MAX_ADS = int(os.getenv("FB_ADS_MAX_ADS", "100"))

# -- LinkedIn Jobs Discovery Config -------------------------------------------
# Keywords to search on LinkedIn Jobs for internship-heavy startups
LINKEDIN_JOBS_KEYWORDS  = os.getenv("LINKEDIN_JOBS_KEYWORDS", "startup intern India")
# How many days back to look for job postings to measure frequency
LINKEDIN_JOBS_LOOKBACK_DAYS = int(os.getenv("LINKEDIN_JOBS_LOOKBACK_DAYS", "14"))
# Minimum number of internship postings by a company to be flagged as a signal
LINKEDIN_JOBS_MIN_POSTINGS  = int(os.getenv("LINKEDIN_JOBS_MIN_POSTINGS", "1"))

# -- Job Platform Discovery (Instahyre, Cutshort, Internshala, Unstop, Indeed) --
# Companies posting many openings within a short window are likely ACTIVELY HIRING,
# which is a strong signal of recent funding (post-funding hiring sprees).
SOURCE_JOB_PLATFORMS = os.getenv("SOURCE_JOB_PLATFORMS", "true").lower() in ("true", "1", "yes")
# Platform display name -> primary search domain (used for site: queries)
JOB_PLATFORM_SITES = {
    "Instahyre":    "instahyre.com",
    "Cutshort":     "cutshort.io",
    "Internshala":  "internshala.com",
    "Unstop":       "unstop.com",
    "Indeed":       "in.indeed.com",
}
# Platform -> search URL used for direct HTTP scraping (fallback when Apify/DDG blocked)
JOB_PLATFORM_URLS = {
    "Instahyre":   "https://www.instahyre.com/jobs/",
    "Cutshort":    "https://cutshort.io/jobs/india",
    "Internshala": "https://internshala.com/internships/",
    "Unstop":      "https://unstop.com/opportunities",
    "Indeed":      "https://in.indeed.com/jobs?q=startup+intern&l=India",
}
JOB_PLATFORM_KEYWORDS  = os.getenv("JOB_PLATFORM_KEYWORDS", "startup intern India hiring")
JOB_PLATFORM_LOOKBACK_DAYS = int(os.getenv("JOB_PLATFORM_LOOKBACK_DAYS", "21"))
JOB_PLATFORM_MIN_POSTINGS  = int(os.getenv("JOB_PLATFORM_MIN_POSTINGS", "1"))
JOB_PLATFORM_MAX_RESULTS   = int(os.getenv("JOB_PLATFORM_MAX_RESULTS", "60"))

# -- Y Combinator Discovery Config -------------------------------------------
# Recent YC batches to target (oldest batches are excluded automatically).
# Format matches the facet values used by ycombinator.com/companies.
YC_RECENT_BATCHES = [
    b.strip() for b in os.getenv(
        "YC_RECENT_BATCHES",
        "Fall 2026,Summer 2026,Spring 2026,Winter 2026,"
        "Fall 2025,Summer 2025,Spring 2025,Winter 2025",
    ).split(",")
]
# Restrict to companies headquartered in these regions (Algolia `regions` facet)
YC_REGIONS_FILTER = [
    r.strip() for r in os.getenv("YC_REGIONS_FILTER", "India,South Asia").split(",")
]
# Cap on YC candidates returned per run
YC_MAX_COMPANIES = int(os.getenv("YC_MAX_COMPANIES", "300"))

# -- Known Unicorns to Exclude -------------------------------------------------
UNICORN_EXCLUSION_LIST = {
    "flipkart", "ola", "ola electric", "swiggy", "zomato", "byju's", "byjus",
    "paytm", "razorpay", "cred", "meesho", "zerodha", "dream11", "dream 11",
    "phonepe", "udaan", "unacademy", "groww", "lenskart", "nykaa", "boat",
    "cars24", "urban company", "urbancompany", "slice", "oyo", "oyo rooms",
    "ola cabs", "rapido", "dunzo", "freshworks", "zoho", "postman", "notion",
    "chargebee", "browserstack", "druva", "icertis", "innovaccer", "moglix",
    "pine labs", "pine labs", "polygon", "matic", "apna", "licious",
    "mamaearth", "honasa", "pharmeasy", "spinny", "vedantu", "eruditus",
    "upgrad", "physics wallah", "physicswallah",
}

# -- Apify Actor IDs -----------------------------------------------------------
APIFY_WEB_SCRAPER_ACTOR   = os.getenv("APIFY_WEB_SCRAPER_ACTOR", "apify/web-scraper")
APIFY_LINKEDIN_JOBS_ACTOR = os.getenv("APIFY_LINKEDIN_JOBS_ACTOR", "curious_coder/linkedin-jobs-scraper")
APIFY_LINKEDIN_PROFILE_SEARCH_ACTOR = os.getenv("APIFY_LINKEDIN_PROFILE_SEARCH_ACTOR", "harvestapi/linkedin-profile-search")
APIFY_LINKEDIN_PROFILE_SCRAPER_ACTOR = os.getenv("APIFY_LINKEDIN_PROFILE_SCRAPER_ACTOR", "harvestapi/linkedin-profile-scraper")
APIFY_FB_ADS_ACTOR = os.getenv("APIFY_FB_ADS_ACTOR", "curious_coder/facebook-ads-library-scraper")
APIFY_CRAWL_TIMEOUT       = int(os.getenv("APIFY_CRAWL_TIMEOUT", "120"))  # seconds
APIFY_MAX_CONCURRENT_RUNS = int(os.getenv("APIFY_MAX_CONCURRENT_RUNS", "5"))

# -- News RSS feeds (used by startup_discovery as supplementary source) --------
NEWS_RSS_FEEDS = {
    "Inc42":       "https://inc42.com/feed/",
    "YourStory":   "https://yourstory.com/feed",
    "Entrackr":    "https://entrackr.com/feed/",
    "TechCrunch":  "https://techcrunch.com/feed/",
}

# -- VC Portfolio URLs (public pages to scan for portfolio companies) -----------
VC_PORTFOLIO_URLS = [
    "https://purevc.fund/portfolio",
    "https://www.100x.vc/portfolio",
    "https://stellarisvp.com/portfolio/",
    "https://www.ivycap.in/portfolio",
    "https://blume.vc/portfolio",
    "https://www.kalaari.com/portfolio",
    "https://www.chiratae.com/portfolio-companies/",
    "https://www.matrixpartners.in/portfolio",
    "https://www.nexusvp.com/portfolio",
    "https://www.lightspeedvp.com/india#portfolio",
]

# -- Wellfound / Product Hunt config ------------------------------------------
WELLFOUND_INDIA_URL = "https://wellfound.com/startups/india"
PRODUCT_HUNT_INDIA_TOPICS = ["india", "made-in-india", "indian-startup"]

# -- Outreach Configuration ----------------------------------------------------
SMTP_HOST             = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT             = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER             = os.getenv("SMTP_USER", "")
SMTP_PASSWORD         = os.getenv("SMTP_PASSWORD", "")
OUTREACH_SENDER_NAME  = os.getenv("OUTREACH_SENDER_NAME", "Outreach Team")
OUTREACH_SENDER_EMAIL = os.getenv("OUTREACH_SENDER_EMAIL", SMTP_USER)
OUTREACH_DAILY_LIMIT  = int(os.getenv("OUTREACH_DAILY_LIMIT", "50"))
OUTREACH_FOLLOWUP_DELAY_DAYS = int(os.getenv("OUTREACH_FOLLOWUP_DELAY_DAYS", "3"))
OUTREACH_MAX_FOLLOWUPS       = int(os.getenv("OUTREACH_MAX_FOLLOWUPS", "2"))
OUTREACH_DRY_RUN             = os.getenv("OUTREACH_DRY_RUN", "true").lower() in ("true", "1", "yes")

# -- Validation ----------------------------------------------------------------
def validate_config():
    issues = []
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        issues.append("GEMINI_API_KEY is not set. Add it to your .env file (free at aistudio.google.com)")
    if not APIFY_API_TOKEN:
        issues.append(
            "APIFY_API_TOKEN is not set. The pipeline will fall back to RSS-only sources. "
            "Get a free token at https://apify.com"
        )
    return issues
