import os
import re
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
    # Founder's Team is a Founder's-Office-family role. Category detection here
    # only ADMITS the post for consideration; whether it is an actual vacancy is
    # decided downstream by ACTUAL_ROLE_STRONG_PATTERNS (explicit vacancy label)
    # vs ROLE_CONTEXT_WEAK_MARKERS — see Phase-9E Bug 2.
    "founder's team",
    "founders team",
    "founder\u2019s team",
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

# ── Explicit Vacancy Labels (Phase 10) ────────────────────────────────────────
# Title-ish framings that by themselves advertise a vacancy ("Role: X",
# "Position: Y", "We're hiring | Z"). These are ADDITIONAL hiring-intent
# evidence used ONLY by the Stage-3 proximity gate in the classifier. They are
# deliberately kept separate from HIRING_SIGNALS (active-hiring verbs) so the
# change stays surgical. They mirror ACTUAL_ROLE_STRONG_PATTERNS, which already
# recognise the same labels as establishing the advertised role — the Stage-3
# gate was rejecting those genuinely label-framed vacancies before the role
# stage could confirm them.
#
#   Role:
#   Position:
#   Job Title:
#   Title:
#   Opening:
#   Vacancy:
#   Designation:
#   Join as
#
# A bare "Role:"/"Position:" WITHOUT a target FO/CoS keyword still cannot reach
# Stage 3 (Stage 2 rejects posts that contain no FO/CoS role keyword), and
# context-only / aggregator / non-target posts are still rejected by Stage 3c,
# Stage 3b and the LLM gate. Adding these labels never widens which POST types
# pass — only which framing of an ALREADY-target-role post satisfies the
# proximity hint.
VACANCY_LABEL_SIGNALS = [
    "role:",
    "position:",
    "job title:",
    "title:",
    "opening:",
    "vacancy:",
    "designation:",
    "join as",
]


# ── Exclusions ────────────────────────────────────────────────────────────────
# Job-seeker detection targets CANDIDATE / opportunity-seeking language, not
# employer hiring language. These are REGEX patterns matched case-insensitively
# against the lowercased post text. They fire only on first-person / open-to-work
# / generic-seeking framing with a seeker OBJECT (job/role/position/opportunity/
# work/employment) — so employer phrases like "we are looking for a Chief of
# Staff" or "actively looking for a strategic partner" are NOT rejected.
EXCLUSIONS_JOB_SEEKER_PATTERNS = [
    # Open-to-work / hire-me / seeking employment (unambiguous job-seeker).
    r"\bopen\s+to\s+work\b",
    r"\bopen\s+to\s+opportuniti\w*\b",
    r"\bhire\s+me\b",
    r"\bhiring\s+myself\b",
    r"\bseeking\s+employment\b",
    r"\bon\s+the\s+(?:job\s+hunt|lookout)\b",
    r"\bin\s+the\s+job\s+market\b",
    # First-person job-seeker seeking an outcome (role/opportunity/job/work/
    # employment), e.g. "I am looking for a Chief of Staff role", "I'm looking
    # for opportunities". Requires the seeker framing (I / I'm / I am).
    r"\b(?:i|i'?m|i am|i\u2019m)\s+(?:am\s+|'m\s+)?"
    r"(?:actively\s+|currently\s+)?(?:looking\s+for|seeking|searching\s+for|"
    r"on\s+the\s+hunt\s+for)\s+"
    r"(?:a\s+|an\s+|the\s+)?[^;\n]{0,45}?"
    r"(?:role|positions?|opportunit\w+|jobs?|work|employment)\b",
    # Candidate generic-seeking ("looking for a role", "seeking opportunities",
    # "looking for positions", "actively looking for opportunities").
    r"\blooking\s+for\s+(?:a\s+|an\s+|the\s+)?(?:role|positions?|opportunit\w+|jobs?)\b",
    r"\bseeking\s+(?:a\s+|an\s+|the\s+)?(?:role|positions?|opportunit\w+)\b",
    r"\bactively\s+(?:looking\s+for|seeking)\s+(?:a\s+|an\s+|the\s+)?"
    r"(?:role|positions?|opportunit\w+|jobs?)\b",
]

# Kept for backward-compatible plain substrings; only exact/safe candidate
# phrases remain (the classifier uses JOB_SEEKER_PATTERNS for the real gate).
EXCLUSIONS_JOB_SEEKER = [
    "open to work", "hire me", "seeking opportunities",
    "seeking a role", "seeking founder's office",
    "open to opportunities",
]

# Employer / recruiter context indicators. When a first-person "I am looking for
# a <role>" post ALSO reads as an employer/recruiter hiring someone (the role is
# positioned FOR an organisation, or there is clear employer/recruiter framing),
# it must NOT be treated as job-seeker language.
#
# STRONG indicators: unambiguous employer/recruiter framing. Matching ANY strong
# pattern classifies the post as employer/recruiter context.
EMPLOYER_RECRUITER_STRONG_PATTERNS = [
    r"\bfor\s+our\s+(?:team|company|startup|org|organization|organisation)\b",
    r"\bfor\s+(?:a\s+|an\s+|the\s+)?(?:[a-z-]+\s+){0,3}(?:startup|company)\b",
    r"\bfor\s+a\s+(?:vanity-priced\s+)?(?:role|position)\b",
    r"\bto\s+join\s+(?:our\s+|the\s+)?(?:team|company|startup|us)\b",
    r"\bto\s+work\s+(?:with|for|alongside)\s+(?:us|our\s+company|our\s+team|the\s+company)\b",
    r"\bwe(?:'re| are)\s+hiring\b",
    r"\bwe(?:'re| are)\s+looking\s+to\s+hire\b",
    r"\bwe(?:'re| are)\s+looking\s+for\b",
    r"\bcandidates?\b",
    r"\bapplicants?\b",
    r"\bapply\s+(?:now|here|via|to|below)\b",
    r"\bsend\s+(?:me\s+)?(?:your|us\s+your)?\s*(?:resume|cv)\b",
    r"\bthe\s+(?:role|position)\s+is\s+open\b",
    r"\bwe\s+have\s+(?:an?\s+)?(?:opening|open\s+role|vacancy|available\s+position|position\s+open)\b",
    r"\binterested\s+(?:applicants?|candidates?)\b",
    r"\b(?:we|i)\s+are?\s+recruiting\b",
    r"\bhiring\s+for\s+the\s+(?:role|position|team)\b",
]

# SUPPORTING indicators: only help confirm employer context when at least one
# STRONG indicator is already present (never sufficient on their own — e.g.
# "DM me" appears in both candidate ("I'm looking for a role, DM me") and
# recruiter posts).
EMPLOYER_RECRUITER_SUPPORT_PATTERNS = [
    r"\bdm\s+me\b",
    r"\breach\s+(?:out|me)\b",
    r"\bcontact\s+me\b",
    r"\bmessage\s+me\b",
    r"\binbox\s+me\b",
    r"\bping\s+me\b",
    r"\binterested\s+in the\s+opening\b",
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

# Phase-9E Bug 1: word-bounded, case-insensitive internship detection replacing
# the old bare-substring match, which wrongly flagged words like
# "international" / "interested" / "internet".
#
#   intern          -> indicator
#   interns         -> indicator
#   internship      -> indicator
#   internships     -> indicator
#   interning       -> indicator
#   international   -> NOT (no \b between "intern" and "ational")
#   interested      -> NOT (word starts "intere", not "intern")
#   internet        -> NOT
#
# "campus hiring" is preserved as a separate non-intern phrase that signals
# fresher/intern-stage hiring (already present in INTERNSHIP_INDICATORS).
INTERNSHIP_RE = re.compile(
    r"\b(?:intern(?:ship|ing)?s?|campus\s+hiring)\b",
    re.IGNORECASE,
)

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

# ── Cold Outreach (app/outreach.py) ───────────────────────────────────────────
# SMTP credentials for the cold-mail step. Nothing sends while DRY_RUN=true,
# regardless of these being set.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
OUTREACH_FROM_EMAIL = os.getenv("OUTREACH_FROM_EMAIL", "")  # falls back to SMTP_USER
OUTREACH_SENDER_NAME = os.getenv("OUTREACH_SENDER_NAME", "Kartik")
# Cap per invocation — mirrors the Apify budget-discipline mindset.
OUTREACH_MAX_PER_RUN = int(os.getenv("OUTREACH_MAX_PER_RUN", "10"))

# ── LLM Quality Gate (app/llm/) ───────────────────────────────────────────────
# LLM_PROVIDER: "groq" | "gemini" | "auto" (auto = first key that is set:
# groq, then gemini; neither set -> gate disabled, everything -> Review).
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").strip().lower()
GROQ_TIMEOUT_S = float(os.getenv("GROQ_TIMEOUT_S", "30"))
GROQ_MAX_RETRIES = int(os.getenv("GROQ_MAX_RETRIES", "1"))

# ── Phase 8: Live Verified Contact Discovery (app/contact) ────────────────────
# CONTACT_PROVIDER: "null" (offline, fail-closed default) | "web_search" (live).
# Live mode performs PUBLIC web lookups ONLY for ACCEPTED leads and is meant
# to be enabled only for an explicitly-approved validation run.
CONTACT_PROVIDER = os.getenv("CONTACT_PROVIDER", "null").strip().lower()
CONTACT_CACHE_PATH = os.getenv("CONTACT_CACHE_PATH", "")
CONTACT_MAX_PAGES = int(os.getenv("CONTACT_MAX_PAGES", "4"))
CONTACT_MAX_REQUESTS = int(os.getenv("CONTACT_MAX_REQUESTS", "8"))
CONTACT_MIN_INTERVAL_S = float(os.getenv("CONTACT_MIN_INTERVAL_S", "0.5"))
CONTACT_TIMEOUT_S = float(os.getenv("CONTACT_TIMEOUT_S", "12"))

# ── Proximity Rule ────────────────────────────────────────────────────────────
# Role keyword and hiring signal must appear within this many characters.
PROXIMITY_CHAR_LIMIT = int(os.getenv("HIRING_PROXIMITY_CHARS", "300"))

# ── Aggregator / roundup detection (Bug C) ────────────────────────────────────
# Conservative deterministic phrases that mark a post as an aggregator /
# roundup / job-alert / job-board rather than a single employer's vacancy.
# These phrases essentially never appear in a genuine single-company vacancy
# announcement, so a single match is safe to reject. Bare words like
# "multiple roles" / "several positions" are deliberately NOT here: a genuine
# employer may hire for several positions at ONE company.
AGGREGATOR_PATTERNS = [
    r"\bhiring alert(s)?\b",
    r"\bjob alert(s)?\b",
    r"\b(?:jobs?|hiring)\s+(?:roundup|digest)s?\b",
    r"\b(?:weekly\s+)?roundup\b",
    r"\bcurated\s+(?:jobs|roles?|opportunities)\b",
    r"\bjob\s+board\b",
    r"\b(?:jobs?|career(?:s)?)\s+list\b",
    r"\bcareer\s+opportunities\b(?!\s+(?:at|with|in)\b)",
    r"\broles?\s+hiring\s+right\s+now\b",
    r"\b\d+\s+(?:roles?|positions?)\s+hiring\b",
    r"\b\d+\s+(?:companies|startups)\s+hiring\b",
    r"\bhere\s+are\s+\d+\s+(?:roles?|jobs?|positions?)\b",
    r"\bcompanies\s+are?\s+hiring\b",
    r"\b(?:jobs?|roles?)?\s*opportunities?\s+roundup\b",
    # ── Phase 9A: structural aggregation phrases ───────────────────────────
    r"\bbatch\s+of\s+\d+\s+roles?\b",
    r"\broles?\s+(?:i|we)\s+(?:went\s+through|reviewed|found|covered|spotted)\b",
    r"\bhere\s+are\s+(?:the\s+)?roles?\b",
    r"\b(?:this|next)\s+week'?s?\s+(?:job|hiring|opportunit\w+)(?:s|roundup)?\b",
    r"\broles?\s+across\s+(?:multiple\s+)?(?:companies|startups)\b",
    r"\bmultiple\s+companies\s+(?:are\s+)?hiring\b",
    r"\bhiring\s+across\s+(?:multiple\s+)?(?:companies|startups)\b",
    r"\bi\s+found\s+these\s+jobs?\b",
    r"\bjob\s+(?:opportunities|listings?)\s+roundup\b",
    r"\b\d{2,}\s+roles?\b",
]

# Phase 9A: subjects of hiring verbs used for single-employer detection. Each
# match yields a candidate employer name; if >=2 UNIQUE employers are found the
# post is a multi-employer roundup, not one vacancy.
MULTI_EMPLOYER_SUBJECT_RE = (
    r"\b((?:[A-Z][A-Za-z0-9&.'\-]*)(?:\s+[A-Z][A-Za-z0-9&.'\-]*){0,3})"
    r"\s+(?:is|are)\s+(?:hiring|looking|recruiting)\b"
)

# Stopwords / generic subjects that must not count as an employer when counting
# distinct hiring subjects (so "we are hiring" or a follow-on "India is hiring"
# do not turn a single-employer post into a fake aggregator).
MULTI_EMPLOYER_STOPWORDS = {
    "we", "we're", "i", "i'm", "it", "they", "you", "he", "she",
    "india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad",
    "pune", "chennai", "now", "our", "this", "that",
}

# Phase 9A: ACTUAL-ROLE validation. A Founders' Office / Chief of Staff keyword
# only counts as a genuine vacancy when the post frames it as the advertised job
# (a title, "hiring/looking for <role>", "join as <role>", an opening, etc.).
# If NONE of these strong patterns match AND a weak CONtext marker exists, the
# keyword is only role-context and the post is rejected, not accepted.
ACTUAL_ROLE_STRONG_PATTERNS = [
    # Hiring / recruiting / looking / seeking + the target role. Tolerates a
    # separator run (space, ':', '|', '-', '–', '—', markdown '*') and an
    # optional article, then the role. A bounded window keeps role-CONTEXT
    # phrases ("hiring a Generalist to work in the Founder's Office") from
    # matching, since the role must appear within the same short clause.
    r"\b(?:hiring|recruiting|looking\s+for|searching\s+for|seeking)\b"
    r"(?:[:\|\-\u2013\u2014*]+|\s+)+"
    r"(?:a|an|for|the|as|of)?\s*"
    r"[^.!?;\n]{0,25}?"
    r"(?:founder'?s\s+office|chief\s+of\s+staff)\b",
    r"\bhiring\s+for\b[^.!?;\n]{0,20}?(?:founder'?s\s+office|chief\s+of\s+staff)\b",
    # Explicit vacancy labels: "Role:/Position:/Job Title:/Title:/Opening:/
    # Vacancy:/Designation:" followed by a separator run (incl. markdown '*')
    # and the role. Label may itself be wrapped in markdown asterisks.
    r"\b(?:\*?\s*(?:role|position|job\s+title|title|opening|"
    r"open\s+(?:role|position)|vacancy|designation)\s*\*?)"
    r"\s*[:\|\-\u2013\u2014*]+\s*"
    r"(?:founder'?s\s+office|chief\s+of\s+staff)\b",
    r"\bjoin(?:ing)?\s+(?:the\s+)?(?:founder'?s\s+office|chief\s+of\s+staff)\b",
    r"\b(?:founder'?s\s+office|chief\s+of\s+staff)\s+"
    r"(?:associate|executive|analyst|lead|head|manager|officer)\b",
    r"\b(?:founder'?s\s+office|chief\s+of\s+staff)\s+(?:associate\s+)?"
    r"(?:is\s+)?(?:open|opening|position|vacancy)\b",
    r"\bfor\s+the\s+role\s+of\s+(?:founder'?s\s+office|chief\s+of\s+staff)\b",
    # Phase-9E Bug 2: explicit Founder's Team vacancy label. A Founder's Team
    # role is only the advertised vacancy when an explicit vacancy label
    # (Role:/Position:/Job Title:/Title:/Opening:/Vacancy:/Designation:)
    # precedes it (optionally followed by "/ Business Operations"). This lets
    # e.g. "Role: Founder's Team / Business Operations" recover genuine
    # vacancies while keeping bare "Founder's Team" mentions and context-only
    # "business operations" language firmly in the weak/context bucket.
    r"\b(?:\*?\s*(?:role|position|job\s+title|title|opening|"
    r"open\s+(?:role|position)|vacancy|designation)\s*\*?)"
    r"\s*[:\|\-\u2013\u2014*]+\s*"
    r"(?:founder'?s\s+team|founders\s+team|founder\u2019s\s+team)"
    r"(?:\s*/\s*business\s+operations)?\b",
]

# Phase 9A: markers that the keyword is ROLE-CONTEXT / experience / preference
# rather than the advertised vacancy. Only consulted when strong evidence is
# ABSENT; never overrides a strong actual-role signal.
ROLE_CONTEXT_WEAK_MARKERS = [
    r"\b(?:founder'?s\s+office|chief\s+of\s+staff)\s+"
    r"(?:role|experience|mindset|environment|team|setting|background|context|"
    r"function|work|type)\b",
    r"\b(?:founder'?s\s+office|chief\s+of\s+staff)\s+(?:experience|mindset|"
    r"skills?|background|prior)\s+(?:preferred|required|a\s+plus|needed)\b",
    r"\b(?:experience|prior|background)\s+(?:in|of)\s+(?:the\s+)?"
    r"(?:founder'?s\s+office|chief\s+of\s+staff)\b",
    r"\bwork(?:ing)?\s+(?:in|within|at|under)\s+the\s+founder'?s\s+office\b",
    r"\b(?:in|within|under)\s+the\s+founder'?s\s+office\b",
    r"\bexperience\s+(?:working\s+)?with\s+founders?\b",
    r"\b(?:high[- ]ownership|impactful|hands-on)\s+.{0,50}?role\b",
    r"\bbusiness\s+operations\b",
    r"\bfounder'?s\s+team\b",
    r"\bfounder'?s\s+office\s+(?:experience|background)\b",
]

# Generic geographic / government terms that can NEVER be a private employer.
# A post whose candidate company is one of these is NOT a single-employer
# vacancy for that term; web-search must not resolve a domain for it (Bug A).
GENERIC_PLACEHOLDER_COMPANIES = {
    "india", "indian", "bangalore", "bengaluru", "mumbai", "delhi",
    "new delhi", "hyderabad", "pune", "chennai", "kolkata", "ahmedabad",
    "jaipur", "gurgaon", "gurugram", "noida", "remote", "remote india",
    "remote - india", "india and global", "global", "worldwide",
}

# Public-sector / government domain suffixes that can NEVER be an employer's
# private hiring-company domain (Bug A). Matched on the registered TLD/second
# level of the candidate domain.
GOV_DOMAIN_SUFFIXES = (
    ".gov", ".gov.in", ".nic.in", ".gov.uk", ".gouv.fr", ".go.jp",
    ".gov.au", ".gov.cn", ".gov.sg", ".ac.in", ".ac.uk", ".edu",
)

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
