"""
schema.py — the Sheet's column layout and every mapping into it.

The layout mirrors the FTB internships reference spreadsheet
(1f6b_QgkmeOAu0XFpiYyDHMgfkXFKEsrX4gl8IjWbI1k), which is READ-ONLY reference material:
it is opened to copy its structure and its matching rows, and is never written to.

Its 20 columns come FIRST, in FTB's exact order, so the two sheets look the same when
opened side by side. Five pipeline audit columns follow. They are appended rather than
substituted because they carry things FTB has no equivalent for — the India evidence
string, the proximity confidence, and the outreach Status — and dropping them to reach a
byte-identical header would destroy the only record of *why* each row was kept. If they
are unwanted, deleting five trailing columns is trivial and reversible; regenerating the
evidence would mean re-paying for the scrape.

Three row sources feed the same layout:

  row_from_post   — a freshly classified LinkedIn post
  row_from_legacy — one of the 124 rows written under the old 15-column schema
  row_from_ftb    — a matching row copied out of the FTB reference sheet

Keeping all three here means the column order is stated exactly once. sheets_writer does
I/O only and derives its dedup column from EXPECTED_HEADERS, so nothing can drift.
"""
import re
from typing import Dict, List, Sequence

# FTB's 20 columns, in FTB's order. Do not reorder: the point is structural parity.
FTB_HEADERS = [
    "Title", "Type", "Timing", "Description", "Stipend", "Duration", "Experience",
    "Location", "Deadline", "Tags", "HiringOrganization", "HiringManager", "Post URL",
    "Form link", "Contact Email", "Date Added", "Hiring Manager linkedin",
    "Similar Fields", "Tag", "Similar Field Tags",
]

# Pipeline audit trail. "Classification Reason" holds the India evidence produced by
# app.geo and is the reason a reviewer can trust a row without opening the post.
AUDIT_HEADERS = [
    "Confidence", "Hiring Intent Signals", "Classification Reason", "Status", "Scraped At",
]

EXPECTED_HEADERS = FTB_HEADERS + AUDIT_HEADERS

# The schema every row used before 2026-08-23. Recognised so the writer can tell a
# pre-migration sheet apart from genuine corruption and name the fix.
LEGACY_HEADERS_V1 = [
    "Post URL", "Post Date", "Scraped At", "Role Category",
    "Employment Type", "Detected Role Title", "Company", "Author Name",
    "Author Profile URL", "Matched Role Keywords", "Hiring Intent Signals",
    "Confidence", "Post Snippet", "Classification Reason", "Status",
]

# Our three role categories expressed in FTB's own Tag vocabulary. Every value below is
# one FTB already uses (Generalist 46 rows, Product 6, Tech 55) — one FTB row is even
# tagged "Generalist (Founder's Office, Chief of Staff, Strategy & Ops, ...)", which is
# our Chief of Staff category verbatim.
FTB_TAG_BY_CATEGORY = {
    "Chief of Staff / Founder's Office / Generalist": "Generalist",
    "Product Manager": "Product",
    "Game Developer": "Tech",
}

# FTB's Type column is WORK MODE (Onsite 248 / Remote 65 / Hybrid 40), not employment
# type. Employment type lives in Timing (Full-time 50 / Part-time 15), which is where our
# employment_type ("Full-time" | "Internship") goes.
_WORK_MODE_FROM_SUFFIX = {"on-site": "Onsite", "onsite": "Onsite", "remote": "Remote", "hybrid": "Hybrid"}

_SUFFIX = re.compile(r"\(([^)]*)\)\s*$")
_EMAIL = re.compile(r"(?<![\w.@/-])([A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")

# Application-form hosts. lnkd.in is included because it is what FTB itself stores in
# "Form link" — LinkedIn rewrites outbound apply links through its own shortener.
_FORM_LINK = re.compile(
    r"https?://(?:"
    r"lnkd\.in/[\w-]+|forms\.gle/[\w-]+|docs\.google\.com/forms/[^\s,)\]]+|"
    r"[\w.-]*typeform\.com/[^\s,)\]]+|tally\.so/[^\s,)\]]+|airtable\.com/[^\s,)\]]+|"
    r"[\w.-]*zohoforms?\.[\w.]+/[^\s,)\]]+|[\w.-]*jotform\.com/[^\s,)\]]+"
    r")",
    re.I,
)

# Currency-prefixed amount first, then the bare "12 LPA" / "5 lakhs" idiom.
_STIPEND_CURRENCY = re.compile(
    r"(?:₹|\bRs\.?\s*|\bINR\s*)\s*[\d,]+(?:\s*(?:[-–]|to)\s*[\d,]+)?(?:\s*(?:k\b|/-|LPA\b|lakhs?\b))?",
    re.I,
)
_STIPEND_IDIOM = re.compile(r"\b\d+(?:\.\d+)?\s*(?:[-–]|to)?\s*(?:\d+(?:\.\d+)?)?\s*(?:LPA\b|lakhs?\b|crores?\b)", re.I)

# Shapes app.geo writes into Classification Reason. Parsed to recover a Location for the
# rows migrated from the 15-column schema, which had no Location column of its own.
_REASON_JOB_LOCATION = re.compile(r"India:\s*job location:\s*([^;]+)", re.I)
_REASON_PLACE_IN_TEXT = re.compile(r"India:\s*India place name in text:\s*'([^']+)'", re.I)


def work_mode(location: str = "", text: str = "") -> str:
    """FTB-vocabulary work mode, or "" when genuinely unknown.

    The LinkedIn job card is authoritative: it ends in a parenthesised mode, e.g.
    "Noida, Uttar Pradesh, India (On-site)". Free text is only consulted when there is no
    card, and an empty string is returned rather than a guess.
    """
    m = _SUFFIX.search(location or "")
    if m:
        mode = _WORK_MODE_FROM_SUFFIX.get(m.group(1).strip().lower())
        if mode:
            return mode

    body = (text or "").lower()
    if re.search(r"\bhybrid\b", body):
        return "Hybrid"
    if re.search(r"\bwork from home\b|\bwfh\b|\bfully remote\b|\bremote\b", body):
        return "Remote"
    if re.search(r"\bwork from office\b|\bon[\s-]?site\b|\bin[\s-]office\b|\bwfo\b", body):
        return "Onsite"
    return ""


def contact_email(text: str) -> str:
    m = _EMAIL.search(text or "")
    return m.group(1) if m else ""


def form_link(text: str) -> str:
    m = _FORM_LINK.search(text or "")
    return m.group(0).rstrip(".,;:") if m else ""


def stipend(text: str) -> str:
    for rx in (_STIPEND_CURRENCY, _STIPEND_IDIOM):
        m = rx.search(text or "")
        if m:
            return re.sub(r"\s+", " ", m.group(0)).strip()
    return ""


def location_from_reason(reason: str) -> str:
    """Recover a Location from an India-evidence string.

    The 124 pre-migration rows predate RawPost.location, so their only surviving
    geography is the evidence app.geo wrote into Classification Reason. "job location: X"
    is the real LinkedIn job card and is used verbatim; a place name found in the post
    text is used as-is, capitalised. Money-marker evidence ("₹", "LPA") names no place,
    so it yields "" rather than an invented one.
    """
    m = _REASON_JOB_LOCATION.search(reason or "")
    if m:
        return m.group(1).strip()
    m = _REASON_PLACE_IN_TEXT.search(reason or "")
    if m:
        return m.group(1).strip().title()
    return ""


def format_date_added(value: str) -> str:
    """Normalise a date to FTB's "YYYY-MM-DD HH:MM", degrading gracefully.

    Inputs seen in practice: "2026-08-12T18:02:25.063Z" (LinkedIn), "2026-08-11"
    (date only), and "" . An unparseable value is passed through untouched rather than
    blanked — losing a date to satisfy a format would be worse than an odd-looking cell.
    """
    v = (value or "").strip()
    if not v:
        return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})", v)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    return v


def _tags(employment_type: str, matched_keywords: str) -> str:
    """FTB's Tags is a free comma list (e.g. "Business Development, Startup, Technology").
    Employment type leads so "Internship" is visible when scanning the column."""
    parts = [p.strip() for p in (employment_type, matched_keywords) if p and p.strip()]
    return ", ".join(parts)


def row_from_post(post, scraped_at: str) -> List[str]:
    """Map a ClassifiedPost onto EXPECTED_HEADERS."""
    text = post.text or ""
    # Resolve Location BEFORE work mode: a location recovered from the India evidence
    # still carries LinkedIn's "(On-site)" / "(Remote)" suffix, which is the most reliable
    # work-mode signal there is. Deriving the mode from the post text first would throw it
    # away and fall back to guessing from prose.
    loc = (getattr(post, "location", "") or "") or location_from_reason(post.classification_reason)
    return [
        post.detected_role_title if post.detected_role_title not in ("", "Unclear") else post.role_category,
        work_mode(loc, text),
        post.employment_type,
        post.post_snippet,
        stipend(text),
        "",                                   # Duration — not extracted yet
        "",                                   # Experience — not extracted yet
        loc,
        "",                                   # Deadline — not extracted yet
        _tags(post.employment_type, post.matched_role_keywords),
        post.company,
        post.author_name,
        post.post_url,
        form_link(text),
        contact_email(text),
        format_date_added(post.post_date),
        post.author_profile_url,
        "",                                   # Similar Fields — FTB taxonomy, not ours
        FTB_TAG_BY_CATEGORY.get(post.role_category, post.role_category),
        "",                                   # Similar Field Tags — FTB taxonomy
        post.confidence,
        post.hiring_intent_signals,
        post.classification_reason,
        post.status,
        scraped_at,
    ]


def row_from_legacy(legacy_row: Sequence[str]) -> List[str]:
    """Map one 15-column row onto EXPECTED_HEADERS.

    Nothing is dropped: every legacy value lands in a column, and Location is recovered
    from the India evidence where the evidence names a place. Columns the old schema never
    captured (Stipend, Duration, Experience, Deadline, Form link, Contact Email) stay
    blank — the full post text is not in the Sheet, only a ~110-char snippet, so there is
    nothing honest to extract them from.
    """
    g = dict(zip(LEGACY_HEADERS_V1, list(legacy_row) + [""] * len(LEGACY_HEADERS_V1)))
    snippet = g["Post Snippet"]
    reason = g["Classification Reason"]
    title = g["Detected Role Title"]
    # Same ordering as row_from_post: the recovered location carries LinkedIn's
    # "(On-site)" suffix, which beats guessing the mode from a 110-char snippet.
    loc = location_from_reason(reason)
    return [
        title if title not in ("", "Unclear") else g["Role Category"],
        work_mode(loc, snippet),
        g["Employment Type"],
        snippet,
        stipend(snippet),
        "", "",
        loc,
        "",
        _tags(g["Employment Type"], g["Matched Role Keywords"]),
        g["Company"],
        g["Author Name"],
        g["Post URL"],
        form_link(snippet),
        contact_email(snippet),
        format_date_added(g["Post Date"]),
        g["Author Profile URL"],
        "",
        FTB_TAG_BY_CATEGORY.get(g["Role Category"], g["Role Category"]),
        "",
        g["Confidence"],
        g["Hiring Intent Signals"],
        reason,
        g["Status"],
        g["Scraped At"],
    ]


def row_from_ftb(ftb_row: Sequence[str], ftb_header: Sequence[str], reason: str,
                 status: str = "New") -> List[str]:
    """Copy an FTB reference row into our layout.

    A straight column-name copy, because the layout was taken from FTB in the first
    place. Only the audit block is synthesised: these rows come from a curated board
    rather than from the proximity classifier, so Confidence is 1.0 and Hiring Intent
    Signals records the source instead of a matched signal phrase.
    """
    src = {name: (ftb_row[i].strip() if i < len(ftb_row) else "")
           for i, name in enumerate(ftb_header) if name.strip()}
    row = [src.get(name, "") for name in FTB_HEADERS]
    row[FTB_HEADERS.index("Date Added")] = format_date_added(src.get("Date Added", ""))
    return row + [1.0, "curated source (FTB internships reference sheet)", reason, status, ""]


def index_of(column: str) -> int:
    """1-based Sheet column index, derived so callers cannot hardcode a stale letter."""
    return EXPECTED_HEADERS.index(column) + 1
