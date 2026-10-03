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
# Phase 24.1: second production tab in the SAME spreadsheet for data roles.
# Routing is category-exclusive (DA/DS → data tab, all other qualified leads →
# main tab). No .env change required for the default.
GOOGLE_SHEET_DATA_WORKSHEET = os.getenv(
    "GOOGLE_SHEET_DATA_WORKSHEET", "Data Analyst & Data Scientist")

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

# ── Phase 17: Data Analyst / Data Scientist role keywords ────────────────────
# Precision-first design (see SEARCH_QUERIES comment + implementation report):
#   * TITLE keywords admit a post at Stage 2 (still gated by proximity +
#     actual-role + India + full-time + LLM, exactly like FO/CoS).
#   * FIELD phrases ("data analytics", "data science", "data & analytics")
#     NEVER admit alone — they name a discipline, not a vacancy. They only
#     count when strong actual-role evidence frames them as the advertised
#     job (handled in the classifier, not here).
#   * "business analyst" is CONDITIONAL — admitted only with nearby
#     data/analytics responsibility evidence (see
#     BUSINESS_ANALYST_DATA_EVIDENCE_TERMS). A generic BA post stays out.
# Matching is substring on normalised lowercase text, so plurals
# ("data analysts") and seniority prefixes ("senior data analyst") match.
DATA_ANALYST_KEYWORDS = [
    "data analyst",
    "analytics analyst",
    "business data analyst",
    "bi analyst",
    "business intelligence analyst",
    "reporting analyst",
    "product data analyst",
    "product analyst",
    "growth data analyst",
    "marketing data analyst",
    "operations data analyst",
    "analytics associate",
    "data analyst intern",
    "junior data analyst",
    "associate data analyst",
    # Phase 25.3 structured taxonomy (§3): ONLY titles with no existing
    # keyword as a substring are added (seniority/domain combos containing a
    # base title already match; e.g. "senior data analyst" ⊂ "data analyst").
    # Bare "analyst" roles (financial/investment/equity/research/credit/
    # operations/HR/systems/security/process/sales/marketing/content) are
    # DELIBERATELY absent — they need explicit data framing (see the
    # conditional "business analyst" rule below), never a bare title hit.
    "data analysis analyst",
    "data analytics specialist",
    "financial data analyst",
    "finance data analyst",
    "risk data analyst",
    "supply chain data analyst",
    "customer data analyst",
    "revenue data analyst",
    "commercial data analyst",
]

DATA_SCIENTIST_KEYWORDS = [
    "data scientist",
    "applied data scientist",
    "product data scientist",
    "machine learning data scientist",
    "ml scientist",
    # Close variants from LinkedIn hiring language (precision-checked):
    # "machine learning scientist" (long form of ML Scientist).
    "machine learning scientist",
    "research data scientist",
    "data science associate",
    "junior data scientist",
    "associate data scientist",
    "data scientist intern",
    "data science intern",
    # Phase 25.3 structured taxonomy (§4): titles with no base substring.
    # Combo framings ("Data Scientist & ML", "Generative AI Data Scientist")
    # already contain "data scientist" and match without new entries.
    "decision scientist",
    "data science specialist",
    "data science engineer",
]

# Phase 25.3 §4: bare "applied scientist" is CONDITIONAL, not automatic.
# It counts as a Data Scientist keyword ONLY with supporting data-science/ML
# evidence in the post. ("applied data scientist" above stays unconditional.)
APPLIED_SCIENTIST_EVIDENCE_TERMS = [
    "machine learning",
    "deep learning",
    "data science",
    "statistical",
    "model development",
    "modeling",
    "modelling",
    "nlp",
    "natural language processing",
    "computer vision",
    "neural network",
    "ai/ml",
    "ml model",
]

# Field/discipline phrases: NOT vacancies by themselves. The classifier admits
# them ONLY with strong actual-role evidence (e.g. "hiring for Data Analytics
# role", "Role: Data Science"). Bare skill/context mentions ("understands
# data analytics", "Python/ML experience") never qualify.
DATA_FIELD_PHRASES = [
    "data analytics",
    "data science",
    "data & analytics",
    "data and analytics",
]

# Conditional "business analyst": counts as a Data Analyst keyword ONLY when
# at least one of these data-responsibility signals is nearby in the post.
# "data" alone is deliberately NOT here (too common); "python/excel" alone
# are skills, not responsibilities (precision rules).
BUSINESS_ANALYST_DATA_EVIDENCE_TERMS = [
    "analytics",
    "sql",
    "power bi",
    "tableau",
    "looker",
    "dashboard",
    "business intelligence",
    "data analytics",
    "reporting",
]

# Phase 25.3 §7/§10: BA negative override. A Business Analyst post framed
# around ERP/requirements/stakeholder work is NOT a data vacancy even when a
# weak evidence term (dashboard/reporting) appears nearby. A STRONG evidence
# term (below) still wins — evidence beats the override.
BA_NEGATIVE_TERMS = [
    "erp",
    " requirement gathering",
    "stakeholder management",
    "uat ",
    "brd",
    " srs",
]

# Phase 25.3 §5: STRONG data evidence. Multiword/ML-specific signals that
# genuinely indicate data-role work. Uses: (a) the Business-Analyst strong
# path (strong beats the BA negative override); (b) the conditional
# "applied scientist" gate. Weighted by semantic relevance: single generic
# words ("data", "analysis") are deliberately NOT here.
STRONG_DATA_EVIDENCE_TERMS = [
    "data analysis",
    "statistical analysis",
    "statistical modeling",
    "predictive modeling",
    "predictive analytics",
    "machine learning",
    "supervised learning",
    "unsupervised learning",
    "deep learning",
    "feature engineering",
    "model development",
    "model evaluation",
    "natural language processing",
    "computer vision",
    "recommendation system",
    "causal inference",
    "data mining",
    "analytical modeling",
    "business intelligence",
    "data science",
    "hypothesis testing",
    "time series",
    "time-series",
]

# Phase 25.3 §6: WEAK data evidence. Skills/tools/context words that MUST
# NEVER alone promote a post to Data Analyst/Data Scientist
# ("Marketing Manager with Excel dashboards" is not a Data Analyst).
# This set is intentionally NOT consulted for admission — it exists so the
# non-promotion rule is pinned by tests, not by convention.
WEAK_DATA_EVIDENCE_TERMS = [
    "dashboard",
    "reporting",
    "insights",
    "metrics",
    "kpi",
    "excel",
    "sql",
    "python",
    "tableau",
    "power bi",
    "visualization",
    "statistics",
]

# Phase 25.3 §7: negative/exclusion taxonomy. Adjacent titles that must NEVER
# be *detected as* data vacancies. Function (controlled, not a blacklist):
# they block FIELD-PHRASE admission ("our data science team" inside a Data
# Engineer post) — exact data-title matches are unaffected, and any post can
# still route through explicit evidence + LLM. Wording chosen so no genuine
# data title contains one as a substring (e.g. "data science engineer" does
# NOT contain "data engineer"; "research data scientist" does NOT contain
# "research scientist").
NON_DATA_VACANCY_TITLES = [
    "machine learning engineer",
    "ml engineer",
    "ai engineer",
    "software engineer",
    "backend engineer",
    "data engineer",
    "analytics engineer",
    "mlops engineer",
    "research engineer",
    "devops engineer",
    "research scientist",
    "data entry",
    "data entry operator",
    "data entry executive",
    "data processing executive",
    "mis executive",
    "mis coordinator",
    "content analyst",
]

# A negative title counts as the ADVERTISED vacancy only with hiring/role
# framing nearby (same vacancy-shape discipline as the positive gates).
_NEG_TITLE_ALT = "|".join(t.replace(" ", r"\s+") for t in NON_DATA_VACANCY_TITLES)
FRAMED_NEGATIVE_TITLE_RE = re.compile(
    r"\b(?:hiring|recruiting|looking\s+for|searching\s+for|seeking|"
    r"role|position|job\s+title|title|opening|vacancy|designation|join\s+as)\b"
    r"[^.!?;\n]{0,40}?\b(?:" + _NEG_TITLE_ALT + r")\b",
    re.IGNORECASE,
)

# Explicit hard exclusion: "data entry" is NEVER a Data Analyst vacancy
# (operator / clerk / associate variants all match the core phrase).
DATA_ENTRY_RE = re.compile(r"\bdata\s*entry\b", re.IGNORECASE)

# Data-role alternation shared by the strong/weak patterns below. Kept in
# sync with the TITLE keyword lists above (plus conditional "business
# analyst", which Stage 2 already gates on evidence). Phase 25.3: bare
# "applied scientist" is EXCLUDED here (it is evidence-conditional at the
# keyword level; letting it fire strong vacancy patterns would bypass that
# gate via field-phrase admission).
_DATA_ROLE_ALT = (
    r"data\s+analyst|analytics\s+analyst|business\s+data\s+analyst|"
    r"bi\s+analyst|business\s+intelligence\s+analyst|reporting\s+analyst|"
    r"product\s+data\s+analyst|product\s+analyst|growth\s+data\s+analyst|"
    r"marketing\s+data\s+analyst|operations\s+data\s+analyst|"
    r"financial\s+data\s+analyst|finance\s+data\s+analyst|"
    r"risk\s+data\s+analyst|supply\s+chain\s+data\s+analyst|"
    r"customer\s+data\s+analyst|revenue\s+data\s+analyst|"
    r"commercial\s+data\s+analyst|data\s+analy(?:sis|tics)\s+analyst|"
    r"data\s+analytics\s+specialist|"
    r"analytics\s+associate|junior\s+data\s+analyst|associate\s+data\s+analyst|"
    r"business\s+analyst|"
    r"data\s+scientist|applied\s+data\s+scientist|product\s+data\s+scientist|"
    r"machine\s+learning\s+data\s+scientist|ml\s+scientist|"
    r"machine\s+learning\s+scientist|"
    r"research\s+data\s+scientist|decision\s+scientist|"
    r"data\s+science\s+associate|data\s+science\s+specialist|"
    r"data\s+science\s+engineer|"
    r"junior\s+data\s+scientist|associate\s+data\s+scientist"
)

# ── Phase 17: Data actual-role validation (mirrors Phase 9A FO/CoS logic) ────
# A data title only counts as a genuine vacancy when framed as the advertised
# job. Strong evidence passes; weak context markers reject ONLY when strong
# evidence is absent (never overriding it).
DATA_ACTUAL_ROLE_STRONG_PATTERNS = [
    r"\b(?:hiring|recruiting|looking\s+for|searching\s+for|seeking)\b"
    r"(?:[:\|\-\u2013\u2014*]+|\s+)+"
    r"(?:a|an|for|the|as|of)?\s*"
    r"[^.!?;\n]{0,25}?"
    r"(?:" + _DATA_ROLE_ALT + r")\b",
    r"\bhiring\s+for\b[^.!?;\n]{0,20}?(?:" + _DATA_ROLE_ALT + r")\b",
    r"\b(?:\*?\s*(?:role|position|job\s+title|title|opening|"
    r"open\s+(?:role|position)|vacancy|designation)\s*\*?)"
    r"\s*[:\|\-\u2013\u2014*]+\s*"
    r"(?:" + _DATA_ROLE_ALT + r")\b",
    r"\bjoin(?:ing)?\s+(?:the\s+|our\s+|as\s+(?:a\s+)?)?"
    r"(?:" + _DATA_ROLE_ALT + r")\b",
    r"\b(?:" + _DATA_ROLE_ALT + r")\s+"
    r"(?:is\s+)?(?:open|opening|position|vacancy)\b",
    r"\bfor\s+the\s+role\s+of\s+(?:" + _DATA_ROLE_ALT + r")\b",
    # Field-phrase vacancy framing ("hiring for a Data Analytics role",
    # "Role: Data Science", "Data Science opening"). Without one of these,
    # a bare field mention is context, never a vacancy.
    r"\b(?:hiring|recruiting|looking\s+for|seeking)\b[^.!?;\n]{0,25}?"
    r"(?:data\s+analytics|data\s+science|data\s+(?:&|and)\s+analytics)\s+"
    r"(?:role|position|opening|vacancy|team\s+role)\b",
    r"\b(?:\*?\s*(?:role|position|job\s+title|title|opening|"
    r"open\s+(?:role|position)|vacancy|designation)\s*\*?)"
    r"\s*[:\|\-\u2013\u2014*]+\s*"
    r"(?:data\s+analytics|data\s+science|data\s+(?:&|and)\s+analytics)\b",
]

# Markers that a data keyword is SKILL/CONTEXT/WRONG-ROLE rather than the
# advertised vacancy. Consulted ONLY when strong evidence is absent.
DATA_ROLE_CONTEXT_WEAK_MARKERS = [
    # "founder who understands data analytics" — the canonical false positive.
    r"\bunderstand(?:s|ing)?\s+(?:data|analytics)\b",
    r"\bknowledge\s+of\s+(?:data|analytics|python|sql|machine\s+learning)\b",
    r"\bwith\s+(?:python|sql|ml|machine\s+learning|analytics)\s+experience\b",
    r"\b(?:data|analytics)\s+(?:experience|mindset|skills?|background)\s+"
    r"(?:preferred|required|a\s+plus|needed|must)\b",
    r"\bexperience\s+(?:in|of|with)\s+(?:data|analytics)\b",
    r"\brequir(?:es|ing)\s+(?:analytics|data\s+analysis)\b",
    r"\bmarketing\s+role\b[^.!?\n]{0,60}\banalytics\b",
    r"\bsoftware\s+engineer\b",
    r"\bdata\s+entry\b",
    r"\bwork\s+(?:closely\s+)?with\s+the\s+data\s+team\b",
    r"\bcollaborat\w+\s+with\s+(?:data|analytics)\b",
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
# Phase 16: bare "seeking" added — genuine employer posts frame vacancies as
# "We are (currently) seeking a <role>" / "seeking a high-calibre Chief of
# Staff" (baseline FN-3 Sonia, FN-4 Dhairya: the hiring verb sits adjacent to
# the role label but no other signal fires). Safe because job-seeker "seeking"
# phrasing ("I'm seeking a role", "seeking opportunities") is rejected EARLIER
# at Stage 1, and "seeking advice" is an informational exclusion below — a
# bare-seeking post still needs role proximity + India + full-time + LLM gates.
HIRING_SIGNALS = [
    "hiring", "we're hiring", "we are hiring", "now hiring",
    "hiring for", "looking for", "we're looking for", "we are looking for",
    "looking to hire", "opening", "open role", "open position",
    "job opening", "join our team", "building our team",
    "team is hiring", "apply", "applications open",
    "send resume", "send your resume", "send cv", "send your cv",
    "dm to apply", "referrals", "referrals welcome",
    "seeking candidates", "seeking",
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
    # "hiring for a role" / "recruiting for a position" - employer framing
    # NOT "looking for a role" (job-seeker) or "searching for a position"
    r"\b(?:hiring|recruiting|seeking)\s+for\s+a\s+(?:vanity-priced\s+)?(?:role|position)\b",
    r"\bto\s+join\s+(?:our\s+|the\s+)?(?:team|company|startup|us)\b",
    r"\bto\s+work\s+(?:with|for|alongside)\s+(?:us|our\s+company|our\s+team|the\s+company)\b",
    # Phase 16: first-person hiring ("I'm hiring again…" — baseline FN-5 Aryan,
    # misflagged as job-seeker) and "we are (currently) seeking" (FN-3 Sonia,
    # FN-4 Dhairya) are employer-side framing. "hiring myself" self-hire stays
    # rejected via ALWAYS_JOB_SEEKER_PATTERNS, which runs unconditionally.
    r"\bi(?:'m| am|\u2019m)\s+hiring\b",
    r"\bwe(?:'re| are|\u2019re)\s+(?:currently\s+|actively\s+)?seeking\b",
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
    # Phase 16: "seeking/looking for advice" is never a hiring announcement.
    # Required companion to bare "seeking" in HIRING_SIGNALS so advice posts
    # ("seeking advice on Founder's Office careers") cannot ride the new
    # signal through the proximity gate.
    "seeking advice", "looking for advice",
]

# Phase 16: UNCONDITIONAL job-seeker / non-vacancy rejections. These fire even
# when employer/recruiter context is present, because the phrasing can never
# describe the author's own vacancy:
#   - "hiring myself" / "hire me": self-hire, not an employer vacancy
#     ("I'm hiring myself out as a ...").
#   - third-person "she/he is looking for a <role>": a candidate
#     recommendation/repost (baseline Human-Residency FP: "She's looking for
#     a Founder's Office role ... talk to Manya"), never the author's vacancy.
ALWAYS_JOB_SEEKER_PATTERNS = [
    r"\bhiring\s+myself\b",
    r"\bhire\s+me\b",
    # Note: "she's/he's/they're" carry NO space before the clitic, so the
    # verb group allows both "she is" and "she's" shapes (baseline Azaan FP:
    # "She's looking for a Founder's Office role … talk to Manya").
    r"\b(?:she|he|they)(?:\s+is|\s+are|['\u2019]s|['\u2019]re)\s+"
    r"looking\s+for\s+"
    r"(?:a\s+|an\s+|the\s+)?[^;\n]{0,40}?"
    r"(?:role|positions?|opportunit\w+|jobs?|work|employment)\b",
    # Agency candidate-marketing ("TALENT SPOTLIGHT … spotlighting Phoebe, an
    # exceptional Chief of Staff … contact grace@…"): supply, not a vacancy.
    r"\btalent\s+spotlight\b",
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
# Phase 16: tier-2 cities + states aligned with app/geo._PLACES (which already
# treats them as India-unique on corpus evidence). Baseline FN-1 (Indore) and
# the Fat-Pig/Enqurious-class posts name these without a metro. "kochi" stays
# OUT (Kochi, Japan collision — see geo.py); "punjab"/"salem" stay OUT.
INDIA_CITIES = [
    "bangalore", "bengaluru", "mumbai", "delhi", "delhi ncr",
    "gurgaon", "gurugram", "noida", "hyderabad", "pune",
    "chennai", "kolkata", "ahmedabad", "jaipur", "india",
    "remote - india", "remote india",
    # Tier-2 additions (Phase 16, mirror geo.py):
    "indore", "coimbatore", "chandigarh", "bhubaneswar",
    "visakhapatnam", "vizag", "nagpur", "surat", "vadodara",
    "lucknow", "kanpur", "bhopal", "thane", "mysuru", "mysore",
    "trivandrum", "thiruvananthapuram", "ranchi", "dehradun",
    "guwahati", "amritsar", "agra", "ghaziabad", "navi mumbai", "raipur",
    # States / union markers (Phase 16, mirror geo.py):
    "karnataka", "maharashtra", "telangana", "tamil nadu", "kerala",
    "gujarat", "rajasthan", "haryana", "uttar pradesh", "west bengal",
    "andhra pradesh", "madhya pradesh", "odisha", "assam", "chhattisgarh",
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

# Phase 16: regex international evidence (checked when no India city matched).
# GBP salaries and .co.uk contacts never belong to an India vacancy
# (baseline C&C Search candidate-spotlight: "Salary: £65,000",
# "grace@candcsearch.co.uk").
NON_INDIA_PATTERNS = [
    r"£\s*\d[\d,]*",
    r"[\w.\-]+\.co\.uk\b",
]

# Phase 16: well-known Indian companies whose NAMED presence as the hiring
# company establishes India relevance when no city/compensation signal exists
# (baseline FN-6: INDmoney Founder's Office Fellow — full-time, genuine, zero
# geo tokens; only the employer name anchors it to India).
# CURATION POLICY (precision-first): word-boundary matched, lowercase
# collision-checked. Ambiguous names are DELIBERATELY excluded: "ola" (ES/PT
# greeting), "cred" (credit/street-cred), "boat"/"noise" (common nouns).
# Additions require corpus evidence of unambiguous Indian-company use.
KNOWN_INDIAN_COMPANIES = [
    "indmoney", "zerodha", "groww", "flipkart", "phonepe", "paytm",
    "razorpay", "swiggy", "zomato", "oyo", "nykaa", "delhivery",
    "meesho", "lenskart", "mamaearth", "dream11", "freshworks", "zoho",
    "browserstack", "postman", "urban company", "infosys", "tcs", "wipro",
    "hcl", "hcltech", "tech mahindra", "tata", "reliance", "jio", "adani",
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
# Canonical GROQ_* names first (as documented in .env.example); legacy
# EXPLABS_* environment names kept as fallback. No behaviour change when only
# one source is set.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "") or os.getenv("EXPLABS_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "") or os.getenv("EXPLABS_MODEL", "deepseek-v4-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Optional second Gemini key (quota rotation). GEMINI_API_KEYS accepts an
# additional comma-separated list. Order: primary, _2, list extras.
GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2", "")
GEMINI_API_KEYS = os.getenv("GEMINI_API_KEYS", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "explabs").strip().lower()
# ── Phase 23: multi-provider availability failover (Groq -> Gemini) ──────────
# LLM_FAILOVER_ORDER: explicit comma-separated chain order, e.g. "groq,gemini".
# Empty (default) = groq->gemini, except LLM_PROVIDER=gemini puts gemini first.
# LLM_PROVIDER=explabs keeps the legacy single-provider path (no failover).
# OPENAI_* is reserved for a future GPT provider (NOT implemented in Phase 23;
# setting these keys today has no effect and never creates a call).
LLM_FAILOVER_ORDER = os.getenv("LLM_FAILOVER_ORDER", "").strip().lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")
GROQ_TIMEOUT_S = float(os.getenv("EXPLABS_TIMEOUT_S", "30"))
GROQ_MAX_RETRIES = int(os.getenv("EXPLABS_MAX_RETRIES", "1"))

# ── Phase 24: Direct-company + verified-HM production gate ──────────────────
# PRODUCTION_GATE: "false" (default — current pipeline behavior, gate available
# for audit/replay only) | "true" (ACCEPT-path leads must also pass
# app.production_gate: identifiable company + verified HM + source policy +
# exact role + confidence >= 0.60; failures are held as Review or rejected and
# never reach the Sheet as New). No .env change required for the default.
PRODUCTION_GATE = os.getenv("PRODUCTION_GATE", "false").lower() in ("true", "1", "yes")

# ── Phase 25.1: ungated-production-write guard ────────────────────────────
# ALLOW_UNGATED_PRODUCTION_WRITE: "0"/unset (default — a REAL non-dry Sheet
# write through the legacy gate-off path is REFUSED fail-closed by the
# orchestrator) | "1" (explicit opt-out for a legacy maintenance write; logs
# loudly). DRY_RUN runs are never affected. No .env change required.
ALLOW_UNGATED_PRODUCTION_WRITE = os.getenv(
    "ALLOW_UNGATED_PRODUCTION_WRITE", "0").lower() in ("true", "1", "yes")

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
    # "hiring alert" / "job alert" ONLY when followed by aggregator language
    # (multiple companies, roles, numbers, "roundup", "list", etc.)
    # A bare "Hiring Alert!" headline is common in genuine single-employer posts.
    r"\bhiring alert(s)?\b.*(?:companies|startups|roles?|positions?|jobs?|roundup|list|\d+\s+(?:roles?|companies|startups))",
    r"\bjob alert(s)?\b.*(?:companies|startups|roles?|positions?|jobs?|roundup|list|\d+\s+(?:roles?|companies|startups))",
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
    # Phase 16: roundup headers name ROLES too ("this week's new startup
    # roles" — baseline SkillPad FP). Genuine single-employer vacancies never
    # use week-roundup framing.
    r"\b(?:this|next)\s+week'?s?\s+(?:job|hiring|opportunit\w+|roles?)(?:s|roundup)?\b",
    # ── Phase 16: mass-hiring / fresher-roundup markers (baseline Stripe FP:
    # "MASS HIRING | BATCH: 2022–2028" + 10 numbered "Company:" entries +
    # "Follow for daily Job & Internship opportunities"). None of these can
    # appear in a genuine single-employer FO/CoS vacancy.
    r"\bmass\s+hiring\b",
    r"\bbatch\s*:\s*20\d{2}",
    r"\bfollow\s+for\s+daily\s+(?:job|hiring|internship)",
    # "For my friends' companies:" + role list (baseline RentOk FP: RentOk
    # roles PLUS friends' companies in one post = multi-employer roundup).
    r"\bfor\s+my\s+friends?['\u2019]?\s+compan",
    r"\bmultiple\s+open\s+roles?\b",
    r"\broles?\s+across\s+(?:multiple\s+)?(?:companies|startups)\b",
    r"\bmultiple\s+companies\s+(?:are\s+)?hiring\b",
    r"\bhiring\s+across\s+(?:multiple\s+)?(?:companies|startups)\b",
    r"\bi\s+found\s+these\s+jobs?\b",
    r"\bjob\s+(?:opportunities|listings?)\s+roundup\b",
    r"\b\d{2,}\s+roles?\b",
    # ── Phase 15B: aggregator patterns from fresh data ───────────────────────
    # "11 fresh roles for anyone looking..." - roundup listing multiple companies
    r"\b\d+\s+fresh\s+roles?\b",
    # "roles in India" / "openings in India" ONLY when used as roundup header
    # Pattern: "X Openings in India (Last 24 Hours)" or "Roles in India - 10 fresh..."
    # Not "We have openings in India for..." (genuine employer)
    r"\b(?:founder'?s\s+office|chief\s+of\s+staff)\s+(?:&|and)?\s*(?:openings?|roles?)\s+in\s+india\b",
    r"\b(?:openings?|roles?)\s+in\s+india\s*[\(-]\s*(?:last\s+\d+\s+hours?|\d+\s+fresh)\b",
    # "fresh roles for anyone" - generic roundup language
    r"\bfresh\s+roles?\s+for\s+anyone\b",
    # "top jobs" / "top roles" - aggregator language
    r"\btop\s+(?:jobs?|roles?|positions?)\s+in\b",
    # "find here:" with link - typical roundup CTA
    r"\bfind\s+here\s*:\s*https?://",
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
    # Phase 16: historical-tense variant ("where he worked in the Founder's
    # Office" — baseline ISB informational shape).
    r"\bworked\s+(?:in|within|at|under)\s+the\s+founder'?s\s+office\b",
    # Phase 16: background-list framing ("positions such as ... Chief of
    # Staff ..." — ISB; "hands-on exposure to ... the Founder's Office" —
    # RK Advisory; "folks from Founder's Office" — SkillPad roundup).
    r"\bsuch\s+as\b[^.!?\n]{0,100}(?:founder'?s\s+office|chief\s+of\s+staff)\b",
    r"\bexposure\s+to\b[^.!?\n]{0,80}(?:founder'?s\s+office|chief\s+of\s+staff)\b",
    r"\bfolks\s+from\s+(?:the\s+)?(?:founder'?s\s+office|chief\s+of\s+staff)\b",
    # Phase 16: reporting-line / wrong-title framing ("work closely with the
    # Chief of Staff to the CEO" + "Manager – CEO's Office" — baseline
    # pooja-rani FP, whose actual vacancy is a support role under the CoS).
    # Chief-of-Staff ONLY: "work with the Founder's Office" usually names the
    # TEAM being joined (baseline Charu intern vacancy carries exactly that
    # line and must keep routing to the internship gate, not the context
    # gate), while "work with the Chief of Staff" names a person-role you
    # support under a different title.
    r"\bwork\s+(?:closely\s+)?with\s+the\s+chief\s+of\s+staff\b",
    r"\bmanager\s*[–\-—:|/]\s*ceo'?s?\s+office\b",
    # Phase 16: explicit Executive-Assistant vacancy ("recruiting for
    # Executive Assistant to Director" — Rehana FP). EA ≠ CoS. Narrow on
    # purpose: "Founder's Office Executive / Executive Assistant" hybrids
    # (baseline Sandhya REVIEW) do NOT match these shapes and still pass.
    r"\brecruiting\s+for\s+(?:an?\s+)?executive\s+assistant\b",
    r"\bexecutive\s+assistant\s+to\s+(?:director|ceo|founder|chairperson|chairman|president|manager|partner)\b",
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
# After Phase 15D audit: removed 4 confirmed zero-yield queries and 2 redundant
# FO hiring queries (exact duplicates). India relevance queries preserved.
#
# Phase 16 expansion — SEARCH COVERAGE ONLY, gate unchanged (deliberate design
# decision, see implementation report §keyword-changes):
#   * The deterministic role gate stays Founder's-Office / Chief-of-Staff
#     (+ Founder's Team) ONLY. Generic adjacent titles (Strategy Associate,
#     Growth Analyst, BizOps, Generalist, ...) are NOT admitted as role
#     keywords: the frozen baseline verifies that wrong-role posts (RK
#     Advisory Sales & Marketing, pooja-rani Manager-CEO's-Office, Rehana EA)
#     must keep failing, and precision-first forbids widening the vacancy
#     gate. Broadening happens at the SEARCH layer — LinkedIn result sets
#     differ per query, so FO/CoS posts carrying strategy/operations/growth/
#     startup wording that the 13 base queries miss are recovered, while every
#     candidate still passes the unchanged FO/CoS + proximity + India +
#     full-time + LLM gates.
#   * Family-title queries (CEO's Office, Founder's Team, Fellow, Chairperson)
#     surface same-family posts; pure non-FO/CoS matches are excluded at
#     Stage 2 exactly as ajay-kumar "Executive to CEO" is today (documented).
#   * Dataset-derived additions: "fellow" (INDmoney Founder's Office Fellow
#     FN-6), "chairperson" (sonia-malhotra CoS-to-Chairperson ₹70 LPA FN-3),
#     "strategy/operations/growth/startup" modifiers (dhairya-gala "Strategic
#     Operations & Growth" FN-4; pooja-rani/skillpad carry the same tags),
#     geo anchors for CoS (Bangalore/Mumbai, mirroring the FO ones).
# Cost note: the actor bills ~$0.00155/post with a 10-post floor per keyword,
# so +15 queries ≈ +150 posts ≈ +$0.23 per daily run at limit=10.
#
# Phase 17 expansion — DATA ANALYST + DATA SCIENTIST (SEARCH COVERAGE ONLY,
# same design as Phase 16 §0.1):
#   * The 28 existing queries above are UNTOUCHED (order + wording pinned).
#   * 10 new queries (5 DA-tagged + 5 DS-tagged) broaden LinkedIn result sets
#     per category; every candidate still passes the unchanged proximity +
#     actual-role + India + full-time + LLM gates, now extended to data roles.
#   * Measurability: each query carries ONE category tag (DA-1..DA-5,
#     DS-1..DS-5) combining the role with a hiring signal or India anchor,
#     so per-query yield / duplicate / candidate / ACCEPT-REVIEW-REJECT rates
#     can be attributed with tools/query_benchmark.py without extra runs.
#   * Cost: +10 queries ≈ +100 posts ≈ +$0.16 per daily run at limit=10
#     (≈$0.59 total for the 38-query set).
SEARCH_QUERIES = [
    '"founder\'s office" hiring',
    '"chief of staff" hiring',
    '"founder\'s office" apply',
    '"chief of staff" apply',
    '"founder associate" hiring',
    '"founder\'s associate" hiring',
    '"office of the founder" hiring',
    '"chief of staff" India',
    '"founder\'s office" India',
    '"founder\'s office" Bangalore',
    '"founder\'s office" Mumbai',
    '"chief of staff" "LPA"',
    '"founder\'s office" "LPA"',
    # ── Phase 16: family-title + modifier expansion (search layer only) ──
    # ── Phase 31: query-precision update (dry-run validated, offline) ──────
    # 8 in-place replacements (count unchanged, 48): ad-magnet queries
    # q30/q31/q35 (Phase 27: high candidate rate, ~0% HM-known, same repost
    # accounts) and zero-yield queries q14/q22/q26/q38/q41 (0 raw posts)
    # now pair role anchors with EMPLOYER-side voice phrases observed in
    # evidence-bearing posts ("we are hiring", "join our team", "is hiring")
    # or an alive geo pattern (q9/q10). Proven families q0/q2/q33/q34/q46
    # untouched. See data/validation/phase31_query_precision/.
    '"founder associate" apply',
    '"chief of staff" "we are hiring"',   # was "office of the founder" apply (0 raw)
    '"founder\'s team" hiring',
    '"office of the CEO" hiring',
    '"CEO\'s office" hiring',
    '"founder\'s office" strategy',
    '"founder\'s office" operations',
    '"founder\'s office" startup',
    '"founder\'s office" growth',
    '"founder\'s office" "is hiring"',     # was "fellow" (0 raw); company-page style
    '"chief of staff" strategy',
    '"chief of staff" startup',
    '"chief of staff" operations',
    '"chief of staff" Mumbai',            # was Bangalore (0 raw); alive geo pattern q9/q10
    '"chief of staff" chairperson',
    # ── Phase 17: Data Analyst (DA-1..DA-5) + Data Scientist (DS-1..DS-5) ──
    # DA broad/CTA/geo + two high-precision variant anchors (BI, product).
    '"data analyst" hiring',              # DA-1 broad vacancy recall
    '"data analyst" apply',               # DA-2 apply-CTA (live-post signal)
    '"data analyst" "we are hiring"',     # DA-3 was "India" (ad-magnet: repost accounts)
    '"business intelligence analyst" "join our team"',  # DA-4 was role+hiring (ad-magnet)
    '"product analyst" hiring',           # DA-5 product-variant precision
    # DS broad/CTA/geo + field-recall + applied-variant precision.
    '"data scientist" hiring',            # DS-1 broad vacancy recall
    '"data scientist" apply',             # DS-2 apply-CTA (live-post signal)
    '"data scientist" "we are hiring"',   # DS-3 was "India" (ad-magnet: repost accounts)
    '"data science" hiring',              # DS-4 field-recall (gate stays strict)
    '"applied scientist" hiring',         # DS-5 applied-variant precision
    # ── Phase 25.5: high-signal hirer-voice layer (HS-MAIN-1..6, HS-DA/DS-1..2)
    # SEARCH COVERAGE ONLY — gates, taxonomy, thresholds all unchanged.
    # Rationale (25.5 pool audit): the candidate pool is HM/company-sparse
    # because role-keyword queries surface anonymous reposts and job-alert
    # accounts. Every query below pairs a ROLE anchor (never voice-alone,
    # never generic jobs/vacancies/alerts/recruitment wording) with
    # first-person hiring voice ("my team", "join our team", "looking for")
    # to bias result sets toward posts with an identifiable hiring-side
    # person. Cost: +10 queries ≈ +100 posts ≈ +$0.16 per run at limit=10.
    # Live per-query validation uses the actor `input` attribution (proven in
    # the 25.5 audit); deferred to the next supervised run — no live fetch
    # was performed to validate these.
    '"founder\'s office" "we are hiring"',  # HS-MAIN-1 was "my team" (0 raw)
    '"chief of staff" "my team"',         # HS-MAIN-2 CoS-voice
    '"founder\'s office" "join our team"',  # HS-MAIN-3 team-voice
    '"chief of staff" "is hiring"',       # HS-MAIN-4 was "join our team" (0 raw)
    '"founder associate" "looking for"',  # HS-MAIN-5 associate-voice
    '"office of the founder" "looking for"',  # HS-MAIN-6 founder-office-voice
    '"data analyst" "my team"',           # HS-DA-1 analyst-voice
    '"data scientist" "my team"',         # HS-DS-1 scientist-voice
    '"data analyst" "join our team"',     # HS-DA-2 analyst-team-voice
    '"data scientist" "join our team"',   # HS-DS-2 scientist-team-voice
]
