"""
extraction.py — Phase 16 deterministic field extraction (OFFLINE, no network).

Single home for the exact-match extraction primitives used by the classifier,
the verifier fold-in, and enrichment:

  - Google Form application links (`extract_google_form_url`)
  - hiring/application emails with a personal-domain purpose gate
    (`extract_emails`, `choose_cold_email`)
  - generic apply links (`extract_apply_link`)
  - full (uncapped) descriptions with structured-field redaction
    (`build_description`)
  - standardized extractive key points, max 4, verbatim (`build_key_points`)
  - shared company-name validation (`clean_company_candidate`,
    `is_generic_descriptor`, `extract_join_company`)
  - hiring-manager helpers (`extract_named_founder`, `extract_recruiter_signature`)

NON-GOALS / SAFETY RULES (precision-first, preserved from earlier phases):
  - NEVER fabricate a URL or email. Every returned value is a literal
    substring of the post text.
  - NEVER resolve shortlinks (lnkd.in etc.): the destination is unknown
    offline, so a shortlink is surfaced only as `apply_link`, never as a
    Google Form.
  - Personal-domain mailboxes (gmail/yahoo/outlook/...) surface ONLY when the
    post explicitly frames that address for hiring/application purposes
    ("send your resume to X", "apply at X", ...). Otherwise they are
    suppressed — a missing email ("Not Available") is a safe outcome.
  - Key points are EXTRACTIVE (verbatim post sentences, lightly cleaned):
    no paraphrase, no invention. Emails and form URLs never appear in them.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# ── Google Forms ─────────────────────────────────────────────────────────────
# Only genuine Google Form URLs count: docs.google.com/forms/... or the
# forms.gle short domain. Anything else (lnkd.in, company career pages,
# "fill this form" with no URL) is NOT a Google Form — fail closed.
_GOOGLE_FORM_RE = re.compile(
    r"https?://(?:www\.)?"
    r"(?:docs\.google\.com/forms/[^\s<>\]\)\"']+"
    r"|forms\.gle/[^\s<>\]\)\"']+)",
    re.IGNORECASE,
)

_TRAILING_URL_PUNCT = ".,;:!?*)]\"'"


def _strip_url(url: str) -> str:
    return (url or "").rstrip(_TRAILING_URL_PUNCT).strip()


def extract_google_form_url(text: str) -> str:
    """Return the first literal Google Form URL in the post, or "".

    Never invents: no URL in text => "". Shortlinks are never resolved.
    """
    if not text:
        return ""
    m = _GOOGLE_FORM_RE.search(text)
    return _strip_url(m.group(0)) if m else ""


def is_google_form_url(url: str) -> bool:
    """True only for literal Google Form URLs (used to validate LLM output)."""
    if not url:
        return False
    return bool(_GOOGLE_FORM_RE.fullmatch(_strip_url(url)))


# ── URLs / apply links ───────────────────────────────────────────────────────
_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+", re.IGNORECASE)

# linkedin.com navigation (posts/profiles/company pages) is never an apply
# link — the post itself is the Source Link. lnkd.in IS allowed: it is
# LinkedIn's generic redirect wrapper, and in hiring posts it almost always
# fronts the employer's form/careers page ("Apply here: https://lnkd.in/...",
# baseline Enqurious/RK/Fellowship). The URL is surfaced verbatim (never
# resolved, never relabeled as a Google Form), so the worst case is a
# mislabeled redirect, not a fabrication.
_LINKEDIN_HOST_RE = re.compile(r"linkedin\.com", re.IGNORECASE)

# A URL counts as an *application* link only with nearby application language.
# (Prevents article / portfolio / "follow us" links from becoming Apply Links.)
_APPLY_CUE_RE = re.compile(
    r"\b(?:apply|application|applications?|register(?:ing|ation)?|"
    r"fill\s+(?:up\s+|out\s+)?(?:this\s+|the\s+)?form|submit\s+(?:your\s+)?"
    r"(?:resume|cv|profile|application)|to\s+apply)\b",
    re.IGNORECASE,
)


def _all_urls(text: str) -> List[Tuple[str, int]]:
    out = []
    for m in _URL_RE.finditer(text or ""):
        url = _strip_url(m.group(0))
        if url:
            out.append((url, m.start()))
    return out


def extract_apply_link(text: str, google_form_url: str = "") -> str:
    """Return the best application URL, or "".

    Precedence (deterministic):
      1. the Google Form URL (it has its own column and is the apply link too)
      2. a non-LinkedIn URL with application language within ~200 chars before
         it ("Apply here: <url>", "Fill the form: <url>", ...)
      3. a non-LinkedIn URL with application language within ~120 chars AFTER
         it ("<url> - apply here with your resume", "Link: <url> To apply,
         send ..."). Phase 20E bounded forward recovery: the window starts
         after the URL end (URL-internal dots never truncate it), is cut at
         the first sentence terminator (.!?) so a later sentence's cue cannot
         capture an unrelated link, uses the SAME cue vocabulary as (2) (no
         new cue words), and never returns LinkedIn profile/post URLs.
         Catches cue-after-link shapes the backward window cannot see;
         anything cue-less still returns "" for the LLM net.
      4. "" — never a bare article/portfolio/follow link, never LinkedIn
         (the post itself is the Source Link).
    """
    if google_form_url:
        return google_form_url
    if not text:
        return ""
    urls = _all_urls(text)
    for url, pos in urls:
        if _LINKEDIN_HOST_RE.search(url):
            continue
        window = text[max(0, pos - 200):pos]
        if _APPLY_CUE_RE.search(window):
            return url
    # Phase 20E: bounded forward recovery (cue follows the link). The window
    # starts AFTER the URL's last character (match end), so dots inside the
    # URL itself can never truncate it; it is then cut at the first sentence
    # terminator (.!?) so a cue belonging to a LATER sentence ("Details at
    # <url>. Apply now by email") does not capture an unrelated link. The
    # start is rewound past URL-glued trailing punctuation (_strip_url trims
    # "…/123." to "…/123" — without the rewind the sentence-ending "." that
    # should block the capture would already be consumed).
    for m in _URL_RE.finditer(text or ""):
        url = _strip_url(m.group(0))
        if not url or _LINKEDIN_HOST_RE.search(url):
            continue
        end = m.end() - (len(m.group(0)) - len(url))
        fwd = text[end:end + 120]
        fwd = re.split(r"[.!?]", fwd, maxsplit=1)[0]
        if _APPLY_CUE_RE.search(fwd):
            return url
    return ""


# ── Emails ───────────────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")

# Personal / consumer mailboxes: NEVER a hiring contact on their own. They
# surface only with explicit in-post application-purpose evidence (see
# _APPLICATION_PURPOSE_RE). Mirrors app/contact/extract._PERSONAL_DOMAINS plus
# common Indian consumer domains. (The enrichment email_resolver stays
# strictest and blocks these unconditionally — fail-closed there.)
PERSONAL_EMAIL_DOMAINS = (
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com",
    "hotmail.com", "outlook.com", "live.com", "msn.com",
    "icloud.com", "me.com", "proton.me", "protonmail.com",
    "zoho.com", "aol.com", "gmx.com", "mail.com",
    "rediffmail.com", "rediff.com", "yandex.com", "inbox.com",
    "example.com", "email.com", "mailinator.com", "yopmail.com",
    "tempmail.com",
)

# Local parts that are never a human/application contact.
_NON_CONTACT_LOCALS = (
    "noreply", "no-reply", "donotreply", "do-not-reply", "unsubscribe",
)

# Explicit framing that a (personal-domain) address is used for
# hiring/application purposes: "send your resume to X", "share your CV at X",
# "apply at X", "interested candidates mail ...", etc.
_APPLICATION_PURPOSE_RE = re.compile(
    r"\b(?:send|share|forward|drop|mail|email|submit)\s+"
    r"(?:me\s+|us\s+)?(?:your|their|the|updated\s+)?\s*"
    r"(?:resume|resumes|cv|profile|application|details)\b"
    r"|\bapply\s+(?:at|to|via|on|here|now)?\s*:?"
    r"|\binterested\s+(?:candidates?|applicants?)\b[^.\n]{0,60}"
    r"\b(?:send|share|mail|email|resume|cv)\b"
    r"|\bresume\s+(?:at|to)\b|\bcv\s+(?:at|to)\b"
    r"|\bto\s+apply\b[^.\n]{0,40}\b(?:email|mail|send)\b",
    re.IGNORECASE,
)


def _domain_of(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def is_personal_domain(addr: str) -> bool:
    d = _domain_of(addr)
    return any(d == p or d.endswith("." + p) for p in PERSONAL_EMAIL_DOMAINS)


def _is_contact_address(addr: str) -> bool:
    if not addr or "@" not in addr or "." not in addr.rsplit("@", 1)[-1]:
        return False
    if addr.split("@", 1)[0].strip().lower() in _NON_CONTACT_LOCALS:
        return False
    return True


def extract_emails(text: str) -> List[str]:
    """All literal email addresses in post order (verbatim, deduped)."""
    if not text:
        return []
    seen = set()
    out = []
    for m in _EMAIL_RE.finditer(text):
        addr = m.group(0).strip().strip(".,;:!?)]}'\"")
        if addr and _is_contact_address(addr) and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr)
    return out


def _has_application_purpose(text: str, pos: int) -> bool:
    """True when hiring/application-purpose language appears near `pos`.

    Window: ~250 chars before (the instruction precedes the address:
    "send your resume to X") through ~80 chars after (rare "X to apply").
    """
    window = text[max(0, pos - 250):pos + 80]
    return bool(_APPLICATION_PURPOSE_RE.search(window))


def choose_cold_email(text: str) -> Tuple[str, str]:
    """Pick the Cold Email for a post.

    Returns (email, evidence_snippet). Deterministic precedence:
      1. first company-domain (non-personal) address in post order
      2. first personal-domain address WITH explicit application-purpose
         evidence nearby
      3. ("Not Available", "") — never fabricated, never a bare personal
         address without hiring-purpose framing.
    """
    if not text:
        return "Not Available", ""
    candidates = [
        (m.group(0).strip().strip(".,;:!?)]}'\""), m.start())
        for m in _EMAIL_RE.finditer(text)
    ]
    candidates = [(a, p) for a, p in candidates if _is_contact_address(a)]
    if not candidates:
        return "Not Available", ""
    for addr, _ in candidates:
        if not is_personal_domain(addr):
            return addr, f"hiring email in post: {addr}"
    for addr, pos in candidates:
        if _has_application_purpose(text, pos):
            return addr, f"application address in post: {addr}"
    return "Not Available", ""


# ── Description (full, uncapped, redacted) ───────────────────────────────────
def _redact_tokens(text: str, tokens: List[str]) -> str:
    out = text
    for tok in tokens:
        if tok and tok in out:
            out = out.replace(tok, "")
    return re.sub(r"\s+", " ", out).strip()


def build_description(text: str) -> str:
    """Full useful post text: whitespace-normalised, NO character cap.

    Emails and Google Form URLs are redacted (they live in their dedicated
    columns and must never be duplicated into Description). Nothing is added,
    nothing is summarised — purely the original wording minus contact tokens.
    """
    if not text:
        return ""
    clean = re.sub(r"\s+", " ", text).strip()
    secrets = extract_emails(clean)
    form = extract_google_form_url(clean)
    if form:
        secrets.append(form)
    return _redact_tokens(clean, secrets)


# ── Key points (standardized, extractive, max 4) ────────────────────────────
# Phase 17: data titles score as role sentences too (FO/CoS shapes pinned
# first — key-point scoring for existing leads is unchanged).
_ROLE_CUE_RE = re.compile(
    r"founder'?s\s+office|founders?\s+office|chief\s+of\s+staff|"
    r"founder'?s\s+associate|office\s+of\s+the\s+founder|ceo'?s?\s+office|"
    r"data\s+scientist|data\s+science|applied\s+scientist|"
    r"ml\s+scientist|machine\s+learning\s+scientist|"
    r"data\s+analyst|analytics\s+analyst|business\s+data\s+analyst|"
    r"bi\s+analyst|business\s+intelligence\s+analyst|reporting\s+analyst|"
    r"product\s+(?:data\s+)?(?:analyst|scientist)|growth\s+data\s+analyst|"
    r"marketing\s+data\s+analyst|operations\s+data\s+analyst|"
    r"analytics\s+associate|business\s+analyst",
    re.IGNORECASE,
)
_HIRING_CUE_RE = re.compile(
    r"\bhiring\b|\bapply\b|\bopening\b|\bjoin\b|\brole\b|\bposition\b",
    re.IGNORECASE,
)
_DETAIL_CUE_RE = re.compile(
    r"\b\d+\s*(?:years?|yrs?|lpa|lakhs?|crore)|\bexperience\b|"
    r"\bctc\b|\bcompensation\b|\bfull[\s‑\-–—]*time\b|\bhybrid\b|\bin[\s‑\-–—]*office\b|"
    r"\bremote\b|\bdeadline\b|\bprobation\b|\bbengaluru\b|\bbangalore\b|"
    r"\bmumbai\b|\bdelhi\b|\bgurgaon\b|\bgurugram\b|\bnoida\b|\bhyderabad\b|"
    r"\bpune\b|\bchennai\b|\bkolkata\b|\bahmedabad\b|\bjaipur\b|\bindore\b|"
    r"\bthane\b|\bindia\b",
    re.IGNORECASE,
)
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_BULLET_PREFIX_RE = re.compile(
    r"^(?:[•\-\*▪●○◦▪►▸✅❌🔹🔸📌📍💼🏢🎓👩‍💻👨‍💻⚡🤝📩📞✈️🚀🔥⭐✔️✓▶️·\d️⃣0-9]+[.)\s]*|"
    r"(?:\d+\s*[.)])\s*)+",
)
_HASHTAG_RE = re.compile(r"#\w+")


def _clean_sentence(s: str) -> str:
    s = _BULLET_PREFIX_RE.sub("", s.strip()).strip()
    s = re.sub(r"\s+", " ", s).strip(" -–—|•,;:")
    return s


def build_key_points(text: str, company: str = "", max_points: int = 4) -> str:
    """Standardized key points: up to `max_points` verbatim post sentences.

    Covers role / hiring framing / company / location-requirements-comp details,
    in post order. Contact sentences (emails / form URLs) are excluded — they
    live in dedicated columns. Hashtags are stripped for readability. Never
    invents: every point is a cleaned substring of the post (or "" when the
    post yields nothing usable).
    """
    if not text:
        return ""
    form_url = extract_google_form_url(text)
    raw_sentences = [s for s in _SENT_SPLIT_RE.split(text) if s and s.strip()]
    scored: List[Tuple[int, int, str]] = []
    for idx, raw in enumerate(raw_sentences):
        if "@" in raw and _EMAIL_RE.search(raw):
            continue  # contact line — dedicated column, not a key point
        if form_url and form_url in raw and len(raw.strip()) < len(form_url) + 40:
            continue  # bare form-link line — dedicated column
        s = _clean_sentence(_HASHTAG_RE.sub("", raw))
        if len(s) < 25 or len(s) > 280:
            continue
        score = 0
        if _ROLE_CUE_RE.search(s):
            score += 3
        if company and company != "Unclear" and company.lower() in s.lower():
            score += 2
        if _HIRING_CUE_RE.search(s):
            score += 1
        if _DETAIL_CUE_RE.search(s):
            score += 1
        if score > 0:
            scored.append((score, idx, s))
    scored.sort(key=lambda t: (-t[0], t[1]))
    picked = sorted(scored[:max_points], key=lambda t: t[1])
    points = []
    for _, _, s in picked:
        s = _redact_tokens(s, extract_emails(s))
        if form_url and form_url in s:
            s = _redact_tokens(s, [form_url])
        if s and "@" not in s:
            points.append("• " + s)
    return "\n".join(points)


# ── Company-name validation (shared by classifier + company_resolver) ───────
_NON_COMPANY_SINGLETONS = {
    "we", "i", "it", "they", "you", "he", "she", "our", "my", "the", "a",
    "an", "this", "that", "re", "ve", "ll", "d", "s", "m", "at", "for",
    "in", "on", "to", "and", "or", "is", "are", "hiring", "jobs", "job",
    # Question/relative words captured before "is/are hiring|looking for"
    # ("Who is hiring …?", "What we are looking for") — never employers.
    "who", "what", "which", "that",
}

# Lowercase connectors allowed INSIDE a name ("Bank of America").
_NAME_CONNECTORS = {"of", "and", "the", "&", "de", "la", "van", "der", "al"}

# Generic descriptor words: a candidate composed ONLY of these is a phrase
# ("Our Team", "a leading startup"), never an employer. (Canonical home of the
# set previously living in app/enrichment/company_resolver.py.)
GENERIC_DESCRIPTOR_WORDS = frozenset({
    "a", "an", "the",
    "fast", "growing", "leading", "top", "new", "well", "known", "reputed",
    "promising", "emerging", "scaling", "bootstrapped", "funded", "backed",
    "financing", "seeded", "venture", "ventures", "early", "stage", "fastest",
    "startup", "startups", "company", "companies", "brand", "brands",
    "ai",
    "business", "businesses", "firm", "firms", "studio", "studios", "agency",
    "agencies", "fintech", "medtech", "healthtech", "edtech", "insurtech",
    "clean", "deep", "saas", "tech", "technology", "technologies", "platform",
    "product", "products", "service", "services", "industry", "industries",
    "digital", "organisation", "organization", "team", "entity",
    "our", "my", "your", "their", "its", "his", "her",
    "pvt", "private", "limited", "ltd", "llp", "llc", "inc", "corp",
    "works", "labs", "group", "systems", "solutions",
})


def is_generic_descriptor(cand: str) -> bool:
    """True when `cand` is only generic words ("Our Team", "a leading
    startup") rather than a named employer. A real brand always carries at
    least one non-generic proper-noun token."""
    words = [w for w in re.split(r"[^a-z0-9]+", (cand or "").lower()) if w]
    if not words:
        return True
    return all(w in GENERIC_DESCRIPTOR_WORDS for w in words)


def clean_company_candidate(raw: str) -> str:
    """Validate a regex-captured company candidate (fail-closed).

    Keeps the leading run of capitalized/digit-led words (plus small
    connectors like "of"/"and"), dropping trailing lowercase prose that
    case-insensitive patterns swallow ("Tracktion where we work" ->
    "Tracktion"). Rejects pronouns, contractions ("re" from "you're"),
    single letters, and generic descriptors. Returns "" when unusable —
    "Unclear" is always safer than junk.
    """
    # A name never spans a line break ("MovieMe\n\nMovieMe" is two mentions,
    # not one employer — keep the first). Split lines BEFORE collapsing
    # whitespace, or the boundary is already lost.
    s = re.split(r"[\r\n]+", (raw or ""))[0]
    s = re.sub(r"\s+", " ", s).strip().strip(".,:;!?\"'")
    if not s:
        return ""
    if s.lower() in {"unclear", "n/a", "na", "none", "unknown", "hiring", "jobs"}:
        return ""
    words = s.split()
    kept: List[str] = []
    for i, w in enumerate(words):
        core = w.strip("&.,'\"-").strip()
        if not core:
            break
        if core[0].isupper() or core[0].isdigit():
            kept.append(w)
        elif core.lower() in _NAME_CONNECTORS and kept:
            kept.append(w)
        else:
            break
    # A trailing connector is swallowed prose, not a name ("Hilary Rhoda
    # Cosmetics and work …" -> "Hilary Rhoda Cosmetics").
    while kept and kept[-1].strip("&.,'\"-").lower() in _NAME_CONNECTORS:
        kept.pop()
    s = " ".join(kept).strip().strip(".,:;!?\"'")
    if not s or len(s) < 2:
        return ""
    if s.lower() in _NON_COMPANY_SINGLETONS:
        return ""
    if is_generic_descriptor(s):
        return ""
    return s


# "Join <Company>," — the employer identifying itself imperatively
# ("Join Enqurious, an AI-native ..."). Stronger than "at <Name>" (which also
# matches clients, platforms, cities: "at Fractal", "at LinkedIn").
# NOTE: intra-name separators are [ \\t] (never \\n): a company name never
# spans a line break, and \\s+ would fuse "MovieMe\\n\\nMovieMe" into one.
_JOIN_COMPANY_RE = re.compile(
    r"\b[Jj]oin\s+([A-Za-z0-9&.'\-]+(?:[ \t]+[A-Za-z0-9&.'\-]+){0,3})"
)


def extract_join_company(text: str) -> str:
    """Employer from a "Join <Company>" imperative, validated. "" if none."""
    if not text:
        return ""
    m = _JOIN_COMPANY_RE.search(text)
    if not m:
        return ""
    return clean_company_candidate(m.group(1))


# "<Company> is/are hiring|looking for" — explicit employer statement.
# The NAME stays case-sensitive (a company name never starts lowercase, and
# clean_company_candidate enforces it fail-closed); only the linking verb is
# case-insensitive, so "Onextal is HIRING" resolves exactly like
# "Acme is hiring" (Phase 18C: explicit employer statements must not stay
# Unclear). Pronoun/subject stopwords can never be the employer.
_IS_HIRING_COMPANY_RE = re.compile(
    r"\b(?!(?:We|I|It|They|You|He|She|Who|What|Which|That)\b)"
    r"([A-Z][A-Za-z0-9&\.\-]*(?:[ \t]+[A-Z][A-Za-z0-9&\.\-]*){0,3})"
    r"\s+(?i:is|are)\s+(?i:hiring|looking\s+for)\b"
)


def extract_is_hiring_company(text: str) -> str:
    """Employer from an explicit "<Company> is hiring" statement, validated.

    Returns "" when unusable — "Unclear" is always safer than junk. The
    intra-name separators are [ \\t], never \\n (a company name never spans a
    line break). Used by the classifier's company block and by the Phase-18
    resolution audit so both share one canonical pattern.
    """
    if not text:
        return ""
    m = _IS_HIRING_COMPANY_RE.search(text)
    if not m:
        return ""
    return clean_company_candidate(m.group(1))


# ── Hiring-manager helpers ───────────────────────────────────────────────────
# "my co-founder Rishi Jain" / "our CEO Jane Doe" — a NAMED person with an
# explicit leadership role, i.e. hiring-side context with a name attached.
_NAMED_LEADER_RE = re.compile(
    r"\b(?:my|our)\s+(?:co[\s\-]*founder|founder|ceo|chief\s+executive|"
    r"managing\s+director)\s+([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){0,2})"
)

# Recruiter signature block: "Pooja Rani | Manager – Talent Acquisition"
# followed (same block) by a phone/email contact. Only with a hiring-side
# title AND a contact token — never a bare name.
# NOTE: intra-name separators are [ \t], never \n — a name never spans a line
# break ("\s+" fused "Full-time.\nVignesh Chandrasekar" into one junk name).
_SIGNATURE_RE = re.compile(
    r"([A-Z][A-Za-z'\-]+(?:[ \t]+[A-Z][A-Za-z'\-]+){1,2})\s*[|｜]\s*"
    r"([^|\n]{2,60}?)\s*(?=\n|$)",
)
# Newline sign-off variant (no pipe): "Vignesh Chandrasekar\nSr HR Executive"
# + email/phone nearby. Confined to the post tail (last ~600 chars) so a
# mid-post employee quote never qualifies.
_SIGNOFF_RE = re.compile(
    r"([A-Z][A-Za-z'\-]+(?:[ \t]+[A-Z][A-Za-z'\-]+){1,2})\s*\n\s*"
    r"((?:Sr\.?|Senior|Junior|Lead|Head|Chief|AVP|VP)\s+)?"
    r"(HR|Human\s+Resources|Talent\s+Acquisition|Recruit(?:er|ment|ing)|"
    r"People(?:\s+(?:Operations|Team|Ops))?|Hiring\s+Manager)"
    # Title remainder stays on its line ([ \t], never \n — otherwise it eats
    # the next line's email local-part and the title check misfires).
    r"[A-Za-z \t&\-]{0,30}",
)


def _name_has_proper_case(name: str) -> bool:
    """Every word starts uppercase AND contains a lowercase letter.

    Excludes ALL-CAPS headers ("WE'RE HIRING | Manager - Talent" is a job
    header, not a recruiter named "WE'RE HIRING") while keeping real names
    ("Pooja Rani", "Vignesh Chandrasekar").
    """
    words = name.split()
    if not 1 <= len(words) <= 3:
        return False
    for w in words:
        core = w.strip("'’-")
        if len(core) < 2 or not core[0].isupper():
            return False
        if not any(c.islower() for c in core):
            return False
    return True
_SIGNATURE_TITLE_RE = re.compile(
    r"\b(?:talent|recruit(?:er|ment|ing)|human\s+resources|\bhr\b|"
    r"hiring|people(?:\s+(?:team|ops|operations))?|staffing)\b",
    re.IGNORECASE,
)
_SIGNATURE_CONTACT_RE = re.compile(r"@|\+?\d[\d\s\-]{7,}\d|📞|📧")


def extract_named_founder(text: str) -> Tuple[str, str]:
    """(name, role-phrase) from "my/our <leader-role> <Name>", else ("","")."""
    if not text:
        return "", ""
    m = _NAMED_LEADER_RE.search(text)
    if not m:
        return "", ""
    name = " ".join(m.group(1).split())
    if len(name) < 3 or not name[0].isupper():
        return "", ""
    start = max(0, m.start() - 0)
    role_phrase = text[m.start():m.start(1)].strip()
    return name, role_phrase


def extract_recruiter_signature(text: str) -> Tuple[str, str]:
    """(name, title) from a recruiter/TA signature block, else ("","").

    Two shapes: pipe form ("Name | Title" + contact) and tail sign-off
    ("Name\\nTitle" + contact). Requires BOTH a hiring-side title
    (talent/recruiter/HR/hiring/people) and a contact token
    (email/phone/emoji) nearby, plus proper name casing (ALL-CAPS headers
    never qualify), so a quoted employee name or candidate sign-off never
    qualifies.
    """
    if not text:
        return "", ""
    for m in _SIGNATURE_RE.finditer(text):
        name = " ".join(m.group(1).split())
        title = " ".join(m.group(2).split())
        if not _SIGNATURE_TITLE_RE.search(title):
            continue
        if not _name_has_proper_case(name):
            continue
        after = text[m.end():m.end() + 120]
        if not _SIGNATURE_CONTACT_RE.search(after):
            continue
        if len(name) < 3:
            continue
        return name, title
    tail = text[-600:] if len(text) > 600 else text
    for m in _SIGNOFF_RE.finditer(tail):
        name = " ".join(m.group(1).split())
        title = " ".join(m.group(0).split("\n")[-1].split())
        if not _SIGNATURE_TITLE_RE.search(title):
            continue
        if not _name_has_proper_case(name):
            continue
        start = len(text) - len(tail) + m.start()
        window = text[max(0, start - 60):start + 200]
        if not _SIGNATURE_CONTACT_RE.search(window):
            continue
        return name, title
    return "", ""
