"""
contact/extract.py — strict, evidence-only extraction primitives (Phase 8).

Pure, OFFLINE functions: given the text/HTML of a PUBLIC page and its URL, they
return ONLY emails and hiring contacts that LITERALLY appear on that page, each
tagged with the exact evidence URL and source tier. They NEVER:
  - generate emails from domain patterns (first@domain, first.last@domain)
  - invent LinkedIn profile URLs from a person's name
  - guess hiring managers from a bare name

EMAIL STANDARD (spec): public mailbox prefix priority is
  careers > hiring > jobs > talent > recruitment/recruiting > official contact.
A named employee email is accepted ONLY when the exact address is literally
displayed on the OFFICIAL company site (reliable public source) — never guessed.

SOURCE PRIORITY (spec): tiers are assigned per page kind:
  TIER 1 official careers/job/contact page
  TIER 2 official website (root/about/team)
  TIER 3..5 reserved (LinkedIn, professional sources, search snippets)
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import List, Optional

from app.contact.schemas import (
    SOURCE_TIER_OFFICIAL_PAGE,
    SOURCE_TIER_OFFICIAL_SITE,
    SOURCE_TIER_SEARCH_SNIPPET,
    VerifiedContact,
    VerifiedEmail,
)

# ── Email literals ─────────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"[\w.+#-]+@[\w.-]+\.[A-Za-z]{2,}")

# Personal / throwaway mailboxes are NEVER a public business contact.
_PERSONAL_DOMAINS = (
    "gmail.com", "yahoo.com", "ymail.com", "hotmail.com", "outlook.com",
    "live.com", "msn.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
    "zoho.com", "aol.com", "gmx.com", "mail.com", "ymail.com",
    "example.com", "email.com", "mailinator.com", "yopmail.com", "tempmail.com",
)

# Public business mailbox priority (highest first — EMAIL STANDARD).
_PUBLIC_RANK = {
    "careers": 1, "career": 1,
    "hiring": 2,
    "jobs": 3,
    "talent": 4,
    "recruit": 5, "recruiting": 5, "recruitment": 5, "recruiter": 5,
    "contact": 6, "hello": 6, "info": 6, "apply": 6, "hr": 6, "work": 6,
    "join": 6, "team": 6,
}
_RANK_MISSING = 99  # named/unknown mailbox: lowest priority, most restricted

_FULL_EMAIL_RE = re.compile(
    r"^[\w.+#-]+@[\w.-]+\.[A-Za-z]{2,}$"
)

# ── LinkedIn / manager extraction ──────────────────────────────────────────────
_LINKEDIN_URL_RE = re.compile(
    r"https?://(?:www\.|[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_]{2,}",
    re.IGNORECASE,
)
_NAME_WORD = r"[A-Z][A-Za-z\u2019'\-]+"
_NAME2 = re.compile(rf"({_NAME_WORD})\s+({_NAME_WORD})")
# The action prefix is matched case-insensitively; the NAME is captured
# case-sensitively (so a trailing lowercase word like "for"/"roles" can never be
# swallowed into the name by re.IGNORECASE and break the person guard).
_REACH_OUT = re.compile(
    r"(?:reach(?:ing)?\s+out\s+to|contact|dm|message|email|speak\s+with|"
    r"drop\s+(?:us|them|me)\s+a\s+line)\s*(?=[A-Z])",
    re.IGNORECASE,
)
_REACH_OUT_NAME = re.compile(
    r"([A-Z][A-Za-z\u2019'\-]+(?:\s+[A-Z][A-Za-z\u2019'\-]+){0,2})\b"
)
_TALENT_ROLE = re.compile(
    r"(?:talent\s+(?:acquisition|lead|partner|manager)|recruit(?:er|ing|ment)|"
    r"people\s+(?:lead|head|partner|manager)|hiring\s+manager|hr\s+(?:lead|head|manager)|"
    r"head\s+of\s+(?:people|talent|hr|talent\s+acquisition)|"
    r"vp\s+of\s+(?:people|talent)|people\s+ops)",
    re.IGNORECASE,
)
_ROLE_SEPARATOR = re.compile(
    r"(?:[&|,\u00b7\u2013\u2014:]\s*|\s+[-|&,]\s+|\(|\[|\"|\\u007c)", re.IGNORECASE,
)

_NAME_BLOCKED = {
    "me", "us", "them", "him", "her", "you", "team", "talent", "people", "hr",
    "careers", "jobs", "hiring", "recruitment", "recruiting", "recruiter",
    "apply", "the", "and", "for", "with", "contact", "dm", "email", "office",
    "linkedin", "profile", "here", "click", "join", "view", "speak", "chat",
}


class _TextExtractor(HTMLParser):
    """Collect visible text, skipping script/style/noscript blocks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self._parts).split())


class _MailtoCollector(HTMLParser):
    """Collect the addr portion of every mailto: link (most reliable signal)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href", "") or ""
        if href.lower().startswith("mailto:"):
            addr = href[len("mailto:") :].split("?")[0].strip().strip("'\"")
            if addr:
                self.hrefs.append(addr)


class _TextWithLinks(HTMLParser):
    """Visible text where LinkedIn /in/ hrefs are INLINED, so name↔URL windows
    are clean text instead of raw HTML (prevents wrong name↔URL pairings)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self._skip_depth += 1
        if tag == "a":
            href = dict(attrs).get("href", "") or ""
            if _LINKEDIN_URL_RE.search(href):
                self._parts.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self._parts).split())


def html_to_text(html: str) -> str:
    """Strip tags/scripts and normalise whitespace. Never panics on junk."""
    if not html:
        return ""
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass  # tolerate malformed HTML; regex fallback still finds emails
    return p.text()


def _mailto_addrs(html: str) -> List[str]:
    p = _MailtoCollector()
    try:
        p.feed(html)
    except Exception:
        return []
    return p.hrefs


def page_kind(url: str) -> str:
    """Classify a URL into a stable kind for source/label mapping."""
    u = (url or "").lower()
    if any(k in u for k in ("/careers", "/career", "/join", "/jobs", "/hiring",
                            "/work-with-us", "/job", "/vacanc", "/position")):
        return "careers"
    if any(k in u for k in ("/contact", "/contacts", "/contact-us", "/enquiry")):
        return "contact"
    if any(k in u for k in ("/team", "/people", "/about", "/leadership")):
        return "about"
    return "site"


_KIND_SOURCE = {
    "careers": "company_careers_page",
    "contact": "company_contact_page",
    "about": "company_site_page",
    "site": "company_website",
}


def _source_label(url: str, kind: str) -> str:
    return _KIND_SOURCE.get(kind, "company_website")


def _source_tier(kind: str) -> int:
    return SOURCE_TIER_OFFICIAL_PAGE if kind in ("careers", "contact") else SOURCE_TIER_OFFICIAL_SITE


def _is_business_domain(domain: str) -> bool:
    d = (domain or "").lower().lstrip("www.")
    if not d:
        return False
    # Label-boundary match: "acme.com" must never be blocked by "me.com".
    for p in _PERSONAL_DOMAINS:
        if d == p or d.endswith("." + p):
            return False
    return True


def _nouns_domain(domain: str) -> str:
    """Registered second-level domain of the official company domain, for
    matching named employee emails (e.g. acme.com -> "acme")."""
    d = (domain or "").lower().lstrip("www.")
    parts = [p for p in d.split(".") if p]
    if len(parts) >= 2:
        d = parts[-2]
    return d


def _rank(addr: str) -> int:
    local = addr.split("@")[0].lower().strip()
    return _PUBLIC_RANK.get(local, _RANK_MISSING)


def extract_emails(html: str, *, page_url: str, official_domain: str = "") -> List[VerifiedEmail]:
    """Return emails that LITERALLY appear on a public page, ordered by the
    EMAIL STANDARD priority. Never generates an address.

    - public mailbox prefixes (careers@/hiring@/jobs@/...) pass from any
      official company page (TIER 1/2), ranked by priority.
    - a NAMED employee address (e.g. rohit.gupta@...) passes ONLY when it is
      literally displayed AND its domain matches the verified official domain
      of the company (tier 1/2, lowest rank).
    """
    raw_candidates: List[str] = []
    for href in _mailto_addrs(html or ""):
        raw_candidates.append(href)
    for m in _EMAIL_RE.finditer(html or ""):
        raw_candidates.append(m.group(0))
    seen: set = set()
    found: List[VerifiedEmail] = []
    company_core = _nouns_domain(official_domain) if _is_business_domain(official_domain) else ""
    kind = page_kind(page_url)
    source = _source_label(page_url, kind)
    tier = _source_tier(kind)
    for addr in raw_candidates:
        a = addr.strip().strip(".;),}")
        if not _FULL_EMAIL_RE.match(a) or a.lower() in seen:
            continue
        seen.add(a.lower())
        local, domain = a.rsplit("@", 1)
        if not _is_business_domain(domain):
            continue  # personal / throwaway mailbox — never a business contact
        prefix = local.lower().strip() in _PUBLIC_RANK
        if not prefix:
            # Named employee email: literal + site matches verified official domain only.
            if not company_core or (company_core != _nouns_domain(domain)):
                continue
            rank, conf, verified = _RANK_MISSING, 0.6, True
        else:
            rank = _rank(a)
            conf = 0.95 if (company_core and _nouns_domain(domain) == company_core) else 0.85
            verified = True
        ctx = _context_snippet(html, a)
        found.append(VerifiedEmail(
            email=a,
            source=source,
            evidence_url=page_url,
            evidence_snippet=ctx[:160],
            source_tier=tier,
            confidence=conf,
            verified=verified,
        ))
    # EMAIL STANDARD priority: lowest rank first.
    found.sort(key=lambda v: (_rank(v.email), -v.confidence))
    return found


def _context_snippet(html: str, token: str, radius: int = 45) -> str:
    if not html:
        return ""
    idx = html.find(token)
    if idx < 0:
        return token
    start = max(0, idx - radius)
    return " ".join(html[start : idx + len(token) + radius].split())


def _looks_like_person(name: str) -> bool:
    tokens = re.split(r"\s+", name.strip())
    if not tokens or len(tokens) > 3:
        return False
    if any(t.lower() in _NAME_BLOCKED for t in tokens):
        return False
    for t in tokens:
        if len(t) < 2:
            return False
        if not re.fullmatch(r"[A-Za-z\u2019'\-]+", t):
            return False
        if not t[0].isupper():
            return False
    # A single capitalized word is only a name if it is clearly not a role stopword.
    return True


def extract_linkedin_url(text: str) -> str:
    """First REAL linkedin.com/in/... URL in the text, or 'Not Available'."""
    m = _LINKEDIN_URL_RE.search(text or "")
    return m.group(0) if m else "Not Available"


def _visible_with_links(html: str) -> str:
    p = _TextWithLinks()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.text()


_NAME_WORD_RE = re.compile(r"[A-Z][A-Za-z\u2019'\-]+")


def _nearest_name_before(text: str, end: int) -> Optional[str]:
    """The capitalized 2-word name standing immediately before a token.

    Uses the LAST TWO capitalized words in the window (nearest pair), so
    "Team Priya Sharma | Head of People" pairs as "Priya Sharma", never
    "Team Priya".
    """
    if not text:
        return None
    window = text[max(0, end - 120):end]
    words = [m.group(0) for m in _NAME_WORD_RE.finditer(window)]
    if len(words) >= 2:
        return " ".join(words[-2:])
    return None


def extract_managers(html: str, *, page_url: str) -> List[VerifiedContact]:
    """Find publicly listed hiring contacts (name + evidence + optional URL).

    Only EXPLICIT signals:
      1. "reach out to <Name> / contact <Name>" text
      2. a real linkedin.com/in/<slug> URL paired with the NEAREST preceding name
      3. a <Name> — <talent|recruiter|people hiring role> entry on team/about pages
    Does NOT treat a bare name or the page owner as a contact.
    """
    text = html_to_text(html)
    visible = _visible_with_links(html) or text
    out: List[VerifiedContact] = []
    seen_names: set = set()
    kind = page_kind(page_url)
    source = _source_label(page_url, kind)
    tier = _source_tier(kind)

    def add(name: str, role: str, url: str, conf: float, snippet: str) -> None:
        n = " ".join(name.split())
        if n.lower() in seen_names or not _looks_like_person(n):
            return
        if n in _NAME_BLOCKED or n.lower() in ("not available", "unclear"):
            return
        seen_names.add(n.lower())
        out.append(VerifiedContact(
            name=n,
            role=role,
            linkedin_url=url,
            evidence_url=page_url,
            evidence_snippet=(snippet or _context_snippet(text or "", n))[:160],
            source_tier=tier,
            confidence=conf,
        ))

    # 1. Explicit "reach out to <Name> / contact <Name>".
    for m in _REACH_OUT.finditer(text or ""):
        after = text[m.end():]
        nm = _REACH_OUT_NAME.match(after)
        if not nm:
            continue
        name = nm.group(1).strip()
        if "_" in name or name.lower() in _NAME_BLOCKED:
            continue
        if _looks_like_person(name):
            url = _linkedin_nearby(text, m.start(), name)
            if url == "Not Available" and visible and visible != text:
                # The real URL may live in the inlined text; find any literal
                # linkedin.com/in/ URL in the same region of visible.
                pos = visible.find(name)
                if pos >= 0:
                    url = _linkedin_nearby(visible, pos, name)
            conf = 0.7 if url != "Not Available" else 0.6
            add(name, "", url, conf, _context_snippet(text or "", name))

    # 2. LinkedIn /in/ URL paired with the NEAREST preceding capitalized name.
    for m in _LINKEDIN_URL_RE.finditer(visible):
        url = m.group(0)
        name = _nearest_name_before(visible, m.start())
        if name and _looks_like_person(name):
            add(name, "", url, 0.7, _context_snippet(visible, url))

    # 3. Team/about page: <Name> — <talent hiring role> entries.
    if kind in ("about", "careers"):
        for m in _TALENT_ROLE.finditer(text or ""):
            name = _nearest_name_before(text, m.start())
            if not name or not _looks_like_person(name):
                continue
            url = _linkedin_nearby(text, m.start(), name)
            conf = 0.8 if url != "Not Available" else 0.6
            add(name, m.group(0).strip(), url, conf, _context_snippet(text or "", name))

    return out


def _linkedin_nearby(text: str, pos: int, name: str) -> str:
    """A real LinkedIn /in/ URL within ~300 chars of `pos`, or Not Available."""
    if not text:
        return "Not Available"
    window = text[max(0, pos - 160) : pos + 260]
    m = _LINKEDIN_URL_RE.search(window)
    return m.group(0) if m else "Not Available"