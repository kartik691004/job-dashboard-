import re
import unicodedata
from typing import Tuple, Dict, Any
from datetime import datetime, timezone

from app.config import (
    FOUNDERS_OFFICE_KEYWORDS,
    CHIEF_OF_STAFF_KEYWORDS,
    DATA_ANALYST_KEYWORDS,
    DATA_SCIENTIST_KEYWORDS,
    DATA_FIELD_PHRASES,
    BUSINESS_ANALYST_DATA_EVIDENCE_TERMS,
    BA_NEGATIVE_TERMS,
    STRONG_DATA_EVIDENCE_TERMS,
    APPLIED_SCIENTIST_EVIDENCE_TERMS,
    FRAMED_NEGATIVE_TITLE_RE,
    DATA_ENTRY_RE,
    DATA_ACTUAL_ROLE_STRONG_PATTERNS,
    DATA_ROLE_CONTEXT_WEAK_MARKERS,
    FO_AMBIGUOUS_KEYWORD,
    FO_PROOF_PHRASES,
    HIRING_SIGNALS,
    VACANCY_LABEL_SIGNALS,
    EXCLUSIONS_JOB_SEEKER,
    EXCLUSIONS_JOB_SEEKER_PATTERNS,
    ALWAYS_JOB_SEEKER_PATTERNS,
    EXCLUSIONS_CONGRATULATORY,
    EXCLUSIONS_INFORMATIONAL,
    INTERNSHIP_RE,
    INDIA_CITIES,
    NON_INDIA_LOCATIONS,
    NON_INDIA_COUNTRIES,
    NON_INDIA_PATTERNS,
    KNOWN_INDIAN_COMPANIES,
    INDIA_COMPENSATION,
    CONFIDENCE_THRESHOLD,
    PROXIMITY_CHAR_LIMIT,
    AGGREGATOR_PATTERNS,
    GENERIC_PLACEHOLDER_COMPANIES,
    MULTI_EMPLOYER_STOPWORDS,
    ACTUAL_ROLE_STRONG_PATTERNS,
    ROLE_CONTEXT_WEAK_MARKERS,
    EMPLOYER_RECRUITER_STRONG_PATTERNS,
    EMPLOYER_RECRUITER_SUPPORT_PATTERNS,
)
from app.extraction import (
    build_description,
    build_key_points,
    choose_cold_email,
    clean_company_candidate,
    extract_apply_link,
    extract_google_form_url,
    extract_is_hiring_company,
    extract_named_founder,
    extract_recruiter_signature,
    extract_join_company,
)
from app.geo import is_india as _geo_is_india
from app.models import RawPost, ClassifiedPost


def _normalize_text(text_lower: str) -> str:
    """Unicode-normalise lowercased post text for MATCHING (1:1 length).

    LinkedIn copy uses curly quotes (’, “ ”), non-breaking spaces, and
    non-breaking/en/em hyphens (‑, –, —). Without normalisation, ASCII signals
    silently miss: "We’re Hiring" never matched "we're hiring", "Full‑Time"
    never matched "full-time", and weak markers written with ' missed curly
    variants. Every replacement is exactly one char, so proximity distances
    and spans stay comparable. Display/description text always uses the
    ORIGINAL post text — this normalised form is matching-only.
    """
    return _fold_styled_alphanumerics(
        text_lower
        .replace("\u2019", "'").replace("\u2018", "'")
        .replace("\u201c", '"').replace("\u201d", '"')
        .replace("\xa0", " ")
        .replace("\u2010", "-").replace("\u2011", "-")
        .replace("\u2012", "-").replace("\u2013", "-")
        .replace("\u2014", "-").replace("\u2212", "-")
    )


# ── Phase 20A: styled-math alphanumeric fold (FN-1 recall fix) ────────────────
# LinkedIn vacancy framings sometimes use Mathematical Alphanumeric Symbols
# ("𝐏𝐫𝐨𝐝𝐮𝐜𝐭", "𝐑𝐨𝐥𝐞:", "𝐋𝐨𝐜𝐚𝐭𝐢𝐨𝐧:" — baseline FN-1 Shiprocket): styled
# codepoints that ARE Latin letters/digits but never match ASCII keywords or
# strong vacancy patterns, so a genuine vacancy falls through to weak-marker
# rejection. Fold ONLY U+1D400–U+1D7FF via NFKD when the decomposition is a
# single ASCII letter/digit (exactly one char out, so 1:1 length holds and
# proximity spans stay comparable). Folded letters are lowercased: str.lower()
# does not touch these caseless compatibility chars, and every matching
# context is lowercase-normalised (or case-insensitive), so an unfolded
# styled capital would otherwise poison matching. Everything else —
# fullwidth forms, enclosed characters, ligatures, emoji, CJK, Greek,
# sub/superscripts — passes through untouched, so no unrelated Unicode can
# newly match a target role. The fold is matching-only: display/description/
# extraction always use the original text.
_MATH_ALNUM_START = 0x1D400
_MATH_ALNUM_END = 0x1D7FF


def _fold_styled_alphanumerics(text: str) -> str:
    """Fold styled-math alphanumerics to lowercase ASCII (matching-only)."""
    if not text:
        return text
    # Fast path: the math block never appears in ordinary posts.
    if not any(_MATH_ALNUM_START <= ord(c) <= _MATH_ALNUM_END for c in text):
        return text
    out = []
    for c in text:
        o = ord(c)
        if _MATH_ALNUM_START <= o <= _MATH_ALNUM_END:
            folded = unicodedata.normalize("NFKD", c)
            if len(folded) == 1 and ("A" <= folded <= "Z"
                                     or "a" <= folded <= "z"
                                     or "0" <= folded <= "9"):
                out.append(folded.lower())
                continue
        out.append(c)
    return "".join(out)


def _is_person_profile_url(url) -> bool:
    """A LinkedIn PERSON profile contains /in/... ; a /company/... or any other
    path is a company / organization page and must not be treated as a person.
    Non-LinkedIn URLs (empty, "Unclear", or any other domain) are conservatively
    treated as NOT a person profile, so nothing is invented."""
    if not url:
        return False
    u = url.lower()
    if "/company/" in u:
        return False
    if "linkedin.com/in/" in u or "/in/" in u:
        return True
    return False


def _distinct_employers(text):
    """Count UNIQUE employer names a post says are hiring / looking / recruiting.

    Returns the number of distinct (normalised) employer names. Used for the
    single-employer guard: >=2 distinct employers => a multi-employer roundup,
    not one vacancy. Stopwords / generic subjects and sentence-fragment capture
    are excluded.

    The input MUST be the original-case post text: capitalization is the signal
    used to recognise company names (MULTI_EMPLOYER_SUBJECT_RE starts each word
    with [A-Z]), and a lowercased input silently disables the guard. Only the
    SUBJECT shape is counted: "<Name> is/are [adverb] hiring/looking/recruiting
    ...". Object-prepositional employers ("... at <Name>") are deliberately NOT
    counted: that pattern cannot reliably separate a genuine second employer
    from an apply-link/platform/role/city noun (e.g. "Apply at LinkedIn"), so
    counting it would over-reject genuine single-employer posts.
    """
    subjects = set()

    # Common adverbial inserts between "is/are" and the verb ("is also hiring",
    # "is currently hiring", "is looking to hire") that previously slipped past.
    subject_re = re.compile(
        r"\b((?:[A-Z][A-Za-z0-9&.'\u2019\-]*)(?:\s+[A-Z][A-Za-z0-9&.'\u2019\-]*){0,2})"
        r"\s+(?:is|are)\s+"
        r"(?:(?:also|now|currently|actively|recently|all)\s+)?"
        r"(?:hiring|recruiting|looking\s+to\s+hire|looking\s+for)\b"
    )

    def _norm(raw):
        name = re.sub(r"\s+", " ", raw).strip()
        # A capitalized-name run captured before "is/are hiring" can accidentally
        # absorb the trailing capitalized word of a PRIOR sentence (e.g.
        # "FirstCry. Flipkart is also hiring"). Wherever a dot-space occurs,
        # keep only the last contiguous segment (the name actually sitting next
        # to the verb), so the real company — not the sentence fragment — counts.
        if re.search(r"\.\s", name):
            name = re.split(r"\.\s", name)[-1].strip()
        name = name.rstrip(".")
        if not name:
            return None
        if name.lower() in MULTI_EMPLOYER_STOPWORDS:
            return None
        return name.lower()

    for m in subject_re.finditer(text):
        n = _norm(m.group(1))
        if n:
            subjects.add(n)
    return len(subjects)


def _has_actual_role_evidence(text):
    """True when the post clearly frames the target keyword as the advertised
    vacancy (a title, 'hiring/looking for <role>', 'join as <role>', an opening).
    Matching is case-insensitive to handle title-case post headers. Styled-math
    framings ("𝐑𝐨𝐥𝐞: ...") are folded first (Phase 20A, matching-only)."""
    return any(re.search(p, _fold_styled_alphanumerics(text or ""), re.IGNORECASE)
               for p in ACTUAL_ROLE_STRONG_PATTERNS)


def _has_role_context_only(text):
    """True when the target keyword appears only as role-context / experience /
    preference (e.g. "high-ownership Founder's Office role", "Founder's Office
    experience preferred"), i.e. NOT a vacancy. Only consulted when strong
    actual-role evidence is absent. Callers pass NORMALISED text so curly-quote
    variants ("Founder’s Office") match the ASCII-written markers."""
    return any(re.search(p, text) for p in ROLE_CONTEXT_WEAK_MARKERS)


def _has_data_actual_role_evidence(text):
    """True when the post frames a DATA title/field as the advertised vacancy
    (mirrors _has_actual_role_evidence for FO/CoS). Case-insensitive.
    Styled-math framings ("𝐑𝐨𝐥𝐞: 𝐏𝐫𝐨𝐝𝐮𝐜𝐭 𝐀𝐧𝐚𝐥𝐲𝐬𝐭") are folded first
    (Phase 20A, matching-only) — otherwise the ASCII strong patterns silently
    miss and a genuine vacancy falls to weak-marker rejection (FN-1)."""
    return any(
        re.search(p, _fold_styled_alphanumerics(text or ""), re.IGNORECASE)
        for p in DATA_ACTUAL_ROLE_STRONG_PATTERNS
    )


def _has_data_role_context_only(text_norm):
    """True when a data keyword appears only as skill/context/wrong-role
    ("understands data analytics", "Software Engineer with ML", "data entry").
    Only consulted when strong data evidence is absent; never overrides it."""
    return any(re.search(p, text_norm) for p in DATA_ROLE_CONTEXT_WEAK_MARKERS)


def _is_data_entry_vacancy(text_norm):
    """True when the ADVERTISED role is data entry (never a Data Analyst
    vacancy). Gated on vacancy framing — a passing "no data entry work"
    mention does not trigger; "hiring Data Entry Operator" / "Role: Data
    Entry" does. Negated clarifications ("not data entry", "no data entry
    work") are stripped first so a genuine "Data Analyst (not data entry)"
    post is never punished for the clarification."""
    if not DATA_ENTRY_RE.search(text_norm):
        return False
    unnegated = re.sub(
        r"\b(?:not|no|non|without)\s+data\s*entry\b", "", text_norm
    )
    if not DATA_ENTRY_RE.search(unnegated):
        return False
    return bool(re.search(
        r"\b(?:hiring|recruiting|looking\s+for|searching\s+for|seeking|"
        r"role|position|job\s+title|title|opening|vacancy|designation|"
        r"join\s+as|operator|clerk|executive|associate)\b"
        r"[^.!?;\n]{0,40}?\bdata\s*entry\b"
        r"|\bdata\s*entry\b[^.!?;\n]{0,25}?"
        r"\b(?:operator|clerk|executive|associate|role|position|"
        r"opening|vacancy)\b",
        unnegated,
    ))


def _detect_data_role(text_norm, post_text):
    """Detect a Data Analyst / Data Scientist vacancy keyword.

    Returns (category, matched_keyword) or ("", ""). Rules (precision-first,
    Phase 25.3 title-first taxonomy):
      * TITLE keywords (DS + DA lists) match directly; earliest-in-post wins
        (longest match wins ties, so "business data analyst" beats the
        "data analyst" substring at the same offset).
      * "business analyst" counts ONLY with data-responsibility evidence
        anywhere in the post (analytics/sql/BI/dashboard/...), and NEVER
        when framed around ERP/requirements/stakeholder work unless a STRONG
        evidence term is also present. A generic BA post yields no match.
      * bare "applied scientist" counts ONLY with supporting data-science/ML
        evidence ("applied data scientist" stays unconditional). A bare
        applied-science mention yields no match.
      * FIELD phrases ("data analytics", "data science", "data & analytics")
        count ONLY with strong actual-role evidence framing them as the
        vacancy AND no framed adjacent-vacancy title (Data/ML Engineer,
        MIS, research scientist, ...) claiming the same post; bare
        discipline/skill mentions yield no match.
    Callers pass text_norm (lowercase normalised) + post_text (original, for
    the case-insensitive strong-evidence check).
    """
    cands = []  # (pos, -len, category, keyword)
    for kw in DATA_SCIENTIST_KEYWORDS:
        pos = text_norm.find(kw)
        if pos != -1:
            cands.append((pos, -len(kw), "Data Scientist", kw))
    for kw in DATA_ANALYST_KEYWORDS:
        pos = text_norm.find(kw)
        if pos != -1:
            cands.append((pos, -len(kw), "Data Analyst", kw))
    # Conditional "business analyst" — data/analytics responsibilities only,
    # with the §7/§10 ERP-flavour negative override (strong evidence wins).
    if "business analyst" in text_norm and not any(
        "business analyst" in c[3] for c in cands
    ):
        _ba_strong = any(ev in text_norm for ev in STRONG_DATA_EVIDENCE_TERMS)
        _ba_weak = any(
            ev in text_norm for ev in BUSINESS_ANALYST_DATA_EVIDENCE_TERMS)
        _ba_neg = any(neg in text_norm for neg in BA_NEGATIVE_TERMS)
        if _ba_strong or (_ba_weak and not _ba_neg):
            pos = text_norm.find("business analyst")
            cands.append((pos, -len("business analyst"), "Data Analyst",
                          "business analyst"))
    # Conditional bare "applied scientist" (§4) — supporting DS/ML evidence
    # required. ("applied data scientist" above already matched if present.)
    if "applied scientist" in text_norm and not any(
        "applied scientist" in c[3] for c in cands
    ):
        if any(ev in text_norm for ev in APPLIED_SCIENTIST_EVIDENCE_TERMS):
            pos = text_norm.find("applied scientist")
            cands.append((pos, -len("applied scientist"), "Data Scientist",
                          "applied scientist"))
    # Field phrases — vacancy-framed only, else a skill mention, not a role;
    # and never when a framed adjacent (non-data) title owns the vacancy.
    if _has_data_actual_role_evidence(post_text):
        if not FRAMED_NEGATIVE_TITLE_RE.search(text_norm):
            for kw in DATA_FIELD_PHRASES:
                pos = text_norm.find(kw)
                if pos != -1 and not any(c[3] == kw for c in cands):
                    cat = ("Data Scientist" if kw == "data science"
                           else "Data Analyst")
                    cands.append((pos, -len(kw), cat, kw))
    if not cands:
        return "", ""
    cands.sort()
    return cands[0][2], cands[0][3]


def _card_india_decision(job_card_location: str):
    """Authoritative job-card geography: "india" | "not_india" | None.

    A card naming a recognised place (or a foreign market) decides ALONE —
    free text is not consulted (app.geo contract). Cards that name no geography
    ("", "Remote", "Hybrid", "job") return None so the text gate decides.
    """
    loc = (job_card_location or "").strip()
    if not loc:
        return None
    india, _ = _geo_is_india("", loc)
    if india:
        return "india"
    # geo returns False both for foreign cards AND unrecognised ones; only a
    # card that actually names a foreign market rejects — anything else falls
    # through to the text gate.
    if _geo_is_india("", loc)[1].startswith("job location outside India"):
        return "not_india"
    return None


def _count_company_labels(text_norm: str) -> int:
    """Occurrences of a "Company:" label line (Stripe-style roundup Key)."""
    return len(re.findall(r"company\s*:", text_norm))


# ── Phase 20B: promo-boilerplate exemption for the "career opportunities" ────
# wire (FN-2 recall fix). Third-party jobs channels append promo footers such
# as "Follow X Jobs for daily hiring updates, career opportunities, and
# industry insights!" to otherwise genuine single vacancies (baseline FN-2:
# Swiggy Data Scientist). The bare phrase is indistinguishable there from a
# real aggregator header — EXCEPT for the promo framing around it. This helper
# returns True ONLY when EVERY "career opportunities" occurrence in the post
# sits inside such a promo clause (follow/subscribe/updates/insights/channel
# markers within ~120 chars before or ~80 after). It exempts NOTHING else:
# every other AGGREGATOR_PATTERN, the Company:-label count, and the structural
# multi-employer guard still run, and the LLM still gates downstream — so a
# genuine multi-role roundup that merely shares a promo footer is still caught
# (pinned by adversarial tests). "wire"/"wires"/"wireless" and other technical
# mentions are unrelated to this phrase and were never matched by it; no
# general "wire" rule is added or removed.
_CAREER_OPPS_RE = re.compile(r"\bcareer\s+opportunities\b")
_PROMO_BOILERPLATE_RE = re.compile(
    r"follow|subscrib|stay\s+tuned|join\s+(?:our\s+)?(?:channel|community)|"
    r"hiring\s+updates|job\s+updates|daily\s+(?:hiring\s+|job\s+)?updates|"
    r"industry\s+insights|regular\s+.*job\s+updates",
)


def _career_opps_only_in_promo_boilerplate(text_norm: str) -> bool:
    """True when each 'career opportunities' mention is promo-footer noise."""
    found = list(_CAREER_OPPS_RE.finditer(text_norm or ""))
    if not found:
        return False
    for m in found:
        window = text_norm[max(0, m.start() - 120):m.end() + 80]
        if not _PROMO_BOILERPLATE_RE.search(window):
            return False
    return True


def _match_india_city(text_norm: str, city: str) -> bool:
    """Letter-boundary city/state match, hashtag-compound aware.

    Bare-substring matching false-fires on short names ("agra" inside
    "instagram" — baseline Kali FP, where it fabricated India relevance).
    Poster-attached location tags ("#MumbaiJobs", "#BhubaneswarJobs") still
    count via the optional trailing jobs-compound. The bare country token is
    NOT handled here (Stage 4 matches it word-boundaried, excluding
    "#IndiaJobs"-style reach hashtags); remote-phrases match by substring.
    """
    if city in ("india",):
        return bool(re.search(r"\bindia(?:n|s)?\b", text_norm))
    if city in ("remote - india", "remote india"):
        return city in text_norm
    return bool(
        re.search(rf"(?<![a-z]){re.escape(city)}(?:jobs?)?(?![a-z])", text_norm)
    )


def _is_employer_recruiter_context(text):
    """True when the post reads as an EMPLOYER / RECRUITER hiring someone rather
    than a job-seeker looking for work.

    Employer intent is confident when at least one STRONG framing pattern is
    present ("for our team", "to join us", "candidates", "we're hiring", ...).
    SUPPORT patterns ("DM me", "reach out", ...) only help confirm context when
    a strong pattern is already present — "DM me" alone is never sufficient,
    because candidates also say "I'm looking for a role, DM me".
    """
    strong_hit = any(re.search(p, text, re.IGNORECASE) for p in EMPLOYER_RECRUITER_STRONG_PATTERNS)
    if strong_hit:
        return True
    # Support alone is not enough: require support AND an extra signal that the
    # "I" is sourcing candidates (a candidate onlooker reference / application
    # framing), so ambiguous first-person text stays fail-closed (rejected).
    support_hit = any(re.search(p, text, re.IGNORECASE) for p in EMPLOYER_RECRUITER_SUPPORT_PATTERNS)
    if support_hit:
        # "DM if interested in the opening/position" — clearly a recruiter
        # pointing applicants at an employer's opening.
        if re.search(r"\b(?:interested|share|refer)\s+.*\bopening\b", text, re.IGNORECASE):
            return True
    return False


class DeterministicClassifier:
    def classify(self, post: RawPost) -> ClassifiedPost:
        text_lower = post.text.lower()
        # Matching-normalised form (curly quotes / exotic dashes / nbsp folded
        # to ASCII, 1:1 length). ALL stage matching below uses text_norm; the
        # original text is used only for display, description, and
        # case-sensitive company-name capture.
        text_norm = _normalize_text(text_lower)
        scraped_at = datetime.now(timezone.utc).isoformat()

# Pre-check: detect employer/recruiter context to avoid false job-seeker rejections
        # This must run BEFORE job-seeker exclusions since "I'm looking for a [role]"
        # can be employer language when combined with "for our team", "candidates", etc.
        employer_context = _is_employer_recruiter_context(text_norm)
        
# ── Stage 1: Exclusions ───────────────────────────────────────────────
        # Phase-9E Bug 1: word-bounded internship detection (INTERNISHIP_RE)
        # replaces the former bare-substring check, so ordinary words like
        # "international", "interested", and "internet" no longer trigger a
        # false internship rejection. Genuine intern/intership/interning mentions
        # still reject WHEN they are NOT in the context of a target FO/CoS role
        # with genuine hiring intent. When a post contains both an internship term
        # AND FO/CoS target-role keywords, the internship mention is part of a
        # target-role opportunity and should proceed to later gates instead of
        # being deterministically rejected.
        #
        # INLINE FO/CoS check: replicate the keyword-detection logic here so
        # the internship guard can decide whether the intern mention is target-
        # role context or pure internship-scrap.
        # Phase 17: data titles also count as target-role context (a "Data
        # Analyst Intern" is a target-role opportunity routing to the LLM for
        # an explicit internship verdict, exactly like FO/CoS interns).
        fo_keyword_found = False
        for kw in FOUNDERS_OFFICE_KEYWORDS:
            if kw in text_norm:
                fo_keyword_found = True
                break
        cos_keyword_found = False
        for kw in CHIEF_OF_STAFF_KEYWORDS:
            if kw in text_norm:
                cos_keyword_found = True
                break
        data_keyword_found = any(
            kw in text_norm
            for kw in (*DATA_ANALYST_KEYWORDS, *DATA_SCIENTIST_KEYWORDS)
        )
        if not data_keyword_found:
            if "business analyst" in text_norm and any(
                ev in text_norm
                for ev in BUSINESS_ANALYST_DATA_EVIDENCE_TERMS
            ):
                data_keyword_found = True
            # Phase 25.3: conditional bare "applied scientist" counts as
            # target-role context only with supporting DS/ML evidence
            # (mirrors _detect_data_role; prevents intern-guard drift).
            elif "applied scientist" in text_norm and any(
                ev in text_norm
                for ev in APPLIED_SCIENTIST_EVIDENCE_TERMS
            ):
                data_keyword_found = True
            elif any(kw in text_norm for kw in DATA_FIELD_PHRASES):
                data_keyword_found = True
        target_role_keyword_found = (
            fo_keyword_found or cos_keyword_found or data_keyword_found
        )
        
        m = INTERNSHIP_RE.search(text_norm)
        if m and not target_role_keyword_found:
            return self._create_invalid(
                post, f"Internship detected: '{m.group(0)}'", scraped_at
            )
        
        # ── Phase 15E: Pay-to-Apply / Scam Risk Gate ───────────────────────
        # High-risk payment signals that indicate a scam. When explicit, route
        # to REJECT. Ambiguous signals route to REVIEW per the project's risk
        # architecture. Unpaid internships are NOT a risk signal.
        PAY_TO_APPLY_HIGH_RISK = [
            r"\bapplication\s+fee\b",
            r"\bregistration\s+fee\b",
            r"\btraining\s+fee\b",
            r"\bsecurity\s+deposit\b",
            r"\bprocessing\s+fee\b",
            r"\bpay\s+to\s+apply\b",
            r"\bpaid\s+placement\b",
            r"\bguaranteed\s+placement\b",
            r"\b100\s*%\s+placement\b",
            r"\bguaranteed\s+job\b",
            r"\bpayment\s+required\s+before\s+joining\b",
            r"\bpaid\s+certification\s+sold\s+as\s+internship\b",
            r"\bcourse\s+disguised\s+as\s+internship\b",
            r"\binternship\s+requires\s+payment\b",
            r"\bfunding\s+required\b",
        ]
        
        for pattern in PAY_TO_APPLY_HIGH_RISK:
            if re.search(pattern, text_norm):
                return self._create_invalid(
                    post,
                    f"Pay-to-apply / scam risk detected: '{pattern}'",
                    scraped_at,
                )
        for ex in EXCLUSIONS_JOB_SEEKER:
            if ex in text_norm:
                return self._create_invalid(post, f"Job-seeker post: '{ex}'", scraped_at)
        # Phase 16: UNCONDITIONAL rejects — phrasing that can never be the
        # author's own vacancy, even with employer context present ("I'm
        # hiring myself out", third-person candidate reposts, talent
        # spotlights). Runs BEFORE the employer-context skip.
        for pat in ALWAYS_JOB_SEEKER_PATTERNS:
            m = re.search(pat, text_norm, re.IGNORECASE)
            if m:
                return self._create_invalid(
                    post,
                    f"Non-vacancy post: '{m.group(0).strip()[:50]}'",
                    scraped_at,
                )
        # Job-seeker pattern exclusions: skip if strong employer/recruiter context detected
        if not employer_context:
            for pat in EXCLUSIONS_JOB_SEEKER_PATTERNS:
                m = re.search(pat, text_norm, re.IGNORECASE)
                if m:
                    return self._create_invalid(
                        post,
                        f"Job-seeker post: candidate seeking language '{m.group(0).strip()[:40]}'",
                        scraped_at,
                    )

        for ex in EXCLUSIONS_CONGRATULATORY:
            if ex in text_norm:
                return self._create_invalid(post, f"Congratulatory/joining post: '{ex}'", scraped_at)

        for ex in EXCLUSIONS_INFORMATIONAL:
            if ex in text_norm:
                return self._create_invalid(post, f"Informational/discussion post: '{ex}'", scraped_at)

        # ── Stage 2: Role Keyword Detection ───────────────────────────────────
        # Phase 17: FO/CoS detection is UNCHANGED (order + values pinned).
        # Data Analyst / Data Scientist detection runs alongside; conflict
        # resolution prefers whichever family carries strong actual-role
        # evidence, defaulting to FO/CoS (so "Founder hiring for a Data
        # Analyst" → Data Analyst, while every FO/CoS-only post behaves
        # exactly as before).
        major_category = ""
        matched_role = ""

        fo_cos_category = ""
        fo_cos_matched = ""
        for keyword in FOUNDERS_OFFICE_KEYWORDS:
            if keyword in text_norm:
                fo_cos_category = "Founder's Office"
                fo_cos_matched = keyword
                break

        if not fo_cos_category:
            for keyword in CHIEF_OF_STAFF_KEYWORDS:
                if keyword in text_norm:
                    fo_cos_category = "Chief of Staff"
                    fo_cos_matched = keyword
                    break

        data_category, data_matched = _detect_data_role(text_norm, post.text)

        if fo_cos_category and data_category:
            # Both families present: the advertised vacancy wins. A data
            # vacancy with FO as mere context ("Founder hiring for a Data
            # Analyst") routes to Data; otherwise FO/CoS keeps precedence
            # so no existing lead changes buckets.
            _fo_strong = _has_actual_role_evidence(post.text)
            _data_strong = _has_data_actual_role_evidence(post.text)
            if _data_strong and not _fo_strong:
                major_category, matched_role = data_category, data_matched
            else:
                major_category, matched_role = fo_cos_category, fo_cos_matched
        elif fo_cos_category:
            major_category, matched_role = fo_cos_category, fo_cos_matched
        elif data_category:
            major_category, matched_role = data_category, data_matched

        if not major_category:
            # Check ambiguous FO
            if FO_AMBIGUOUS_KEYWORD in text_norm.split():
                # Strict proof check
                has_proof = any(proof in text_norm for proof in FO_PROOF_PHRASES)
                if has_proof:
                    major_category = "Founder's Office"
                    matched_role = "fo (with proof)"
                else:
                    return self._create_invalid(post, "FO keyword found but no explicit Founder's Office proof phrase", scraped_at)
            else:
                return self._create_invalid(post, "No relevant role keyword found", scraped_at)

        # Phase 17: a data-entry vacancy is never a Data Analyst lead, even
        # when a data keyword matched nearby ("Data Analyst (not data entry)"
        # still passes — the vacancy-shape gate below requires data-entry to
        # be the advertised role, not a passing mention).
        if major_category in ("Data Analyst", "Data Scientist"):
            if _is_data_entry_vacancy(text_norm):
                return self._create_invalid(
                    post,
                    "Data-entry role, not a Data Analyst/Data Scientist vacancy",
                    scraped_at,
                )

        # ── Stage 3: Hiring Intent Validation ─────────────────────────────────
        matched_signal = ""
        min_distance = float('inf')
        
        # We need to find the closest hiring signal to the matched role keyword
        # Since 'matched_role' might be "fo (with proof)", we look for the actual words
        search_roles = [matched_role] if matched_role != "fo (with proof)" else [FO_AMBIGUOUS_KEYWORD] + FO_PROOF_PHRASES

        for role_term in search_roles:
            if role_term not in text_norm: continue
            for role_match in re.finditer(re.escape(role_term), text_norm):
                role_start, role_end = role_match.span()
                # Phase 10: explicit vacancy labels (Role:/Position:/Title:/
                # Opening:/Vacancy:/Designation:/Join as) also satisfy the
                # proximity hint, alongside the active-hiring verbs. They never
                # widen which POST types pass (Stage 2 already requires a target
                # FO/CoS keyword, and Stages 3b/3c still reject aggregators,
                # context-only, and non-target roles). This only lets genuinely
                # label-framed vacancies reach the later gates.
                for sig in (*HIRING_SIGNALS, *VACANCY_LABEL_SIGNALS):
                    for sig_match in re.finditer(re.escape(sig), text_norm):
                        sig_start, sig_end = sig_match.span()
                        dist = max(0, max(sig_start - role_end, role_start - sig_end))
                        if dist < min_distance:
                            min_distance = dist
                            matched_signal = sig

        if min_distance > PROXIMITY_CHAR_LIMIT:
            return self._create_invalid(post, f"No genuine hiring intent found (Distance: {min_distance} chars)", scraped_at)

        # Subject direction check (I'm looking vs We're looking) - already largely handled by EXCLUSIONS_JOB_SEEKER
        # But let's add a strict check for "I am looking for a [role]" pattern just in case
        job_seeking_pattern = re.compile(rf"(i am|i'm|i\u2019m) looking for a (.*?)({re.escape(matched_role.replace(' (with proof)', ''))})", re.IGNORECASE)
        if job_seeking_pattern.search(text_norm):
            # "I am looking for a Chief of Staff" can be either a JOB SEEKER
            # (wanting the role for themselves) or a RECRUITER/EMPLOYER (sourcing
            # a candidate for an organisation). Only reject as a job seeker when
            # there is NO strong employer/recruiter context; otherwise let the
            # post proceed through the normal hiring/role/India/full-time gates.
            if not _is_employer_recruiter_context(text_norm):
                return self._create_invalid(post, "First-person job seeking pattern detected", scraped_at)


        # ── Stage 3b: Single-employer / Aggregator validation (Phase 9A) ───────
        # A post that is a fixed list of jobs across MANY employers (roundups,
        # job alerts, curated lists, "batch of 40 roles", "X companies hiring")
        # is NOT a single-employer vacancy. It must be rejected BEFORE any field
        # extraction / enrichment, so an aggregator's company can never leak into
        # a lead. Two independent checks:
        #   (1) explicit aggregator phrasing;
        #   (2) >=2 distinct employers each "is/are hiring" (structural).
        #   (3) >=3 "Company:" labels — a numbered multi-company listing
        #       (baseline Stripe FP: 10 "N. Company: X" entries in one post).
        for agg_re in AGGREGATOR_PATTERNS:
            # Phase 20B: the "career opportunities" wire alone is skipped when
            # every occurrence is third-party promo-footer noise (FN-2); all
            # other wires and guards still apply to the same post.
            if (agg_re.startswith(r"\bcareer\s+opportunities\b")
                    and _career_opps_only_in_promo_boilerplate(text_norm)):
                continue
            if re.search(agg_re, text_norm):
                return self._create_invalid(
                    post,
                    f"Aggregator/roundup post detected: '{agg_re}'",
                    scraped_at,
                )
        if _count_company_labels(text_norm) >= 3:
            return self._create_invalid(
                post,
                "Multi-employer aggregator: repeated Company listings in one post",
                scraped_at,
            )
        # NOTE: _distinct_employers relies on original-case capitalization to
        # identify company names (MULTI_EMPLOYER_SUBJECT_RE starts each word with
        # [A-Z]). Passing text_lower here would zero every match and silently
        # disable the structural multi-employer guard. Use post.text (original
        # case) so the guard actually fires.
        if _distinct_employers(post.text) >= 2:
            return self._create_invalid(
                post,
                "Multi-employer aggregator: multiple distinct companies hiring in one post",
                scraped_at,
            )

        # ── Stage 3c: Actual-role validation (Phase 9A) ────────────────────────
        # CORE PRODUCT RULE: the target role must be the ADVERTISED VACANCY in
        # this post, not merely a mention / context / experience / preference.
        # A genuine vacancy is signalled by strong patterns ("hiring a Founder's
        # Office Associate", "role: Chief of Staff", "looking for a Chief of
        # Staff", "join as <role>", "<role> Executive", "<role> is open").
        # If strong evidence is present the post passes (even when it also says
        # "business operations" or mentions experience). If strong evidence is
        # ABSENT and the keyword only appears as context/experience/preference,
        # reject — "Founder's Office role" in a Marketing Manager post, or a
        # "Business Operations / Founder's Team" role, is not a FO/CoS vacancy.
        if not _has_actual_role_evidence(post.text):
            if _has_role_context_only(text_norm):
                return self._create_invalid(
                    post,
                    "FO/CoS mentioned as role context/experience, not the advertised vacancy",
                    scraped_at,
                )

        # ── Stage 3c-data: Data actual-role validation (Phase 17) ─────────────
        # Mirrors the FO/CoS rule above for Data Analyst / Data Scientist:
        # the data title must be the ADVERTISED VACANCY, not a skill mention
        # ("understands data analytics"), a wrong-role requirement
        # ("Software Engineer with Python/ML"), or a data-entry job. Strong
        # evidence passes even with weak markers present; weak-only rejects.
        # Field-phrase and conditional-BA matches additionally require strong
        # evidence (a bare "we use data analytics" is never a vacancy).
        # FO/CoS-only posts skip this block entirely (behaviour unchanged).
        if major_category in ("Data Analyst", "Data Scientist"):
            _data_strong = _has_data_actual_role_evidence(post.text)
            if not _data_strong:
                if _has_data_role_context_only(text_norm):
                    return self._create_invalid(
                        post,
                        "Data role mentioned as skill/context/requirement, not the advertised vacancy",
                        scraped_at,
                    )
                if matched_role in DATA_FIELD_PHRASES or \
                        matched_role == "business analyst":
                    return self._create_invalid(
                        post,
                        "Data field/BA mention without vacancy framing, not the advertised role",
                        scraped_at,
                    )

        # ── Stage 4: Market Classification (Strictly India-Only) ──────────────
        india_relevance = "Unclear"
        market = ""
        india_evidence = ""
        
        has_india_city = False
        has_remote = False
        has_intl_loc = False
        has_india_comp = False

        # Phase 16: authoritative job-card geography FIRST. A card naming a
        # recognised Indian place (Midgrow FN-1: "Indore, Madhya Pradesh, India
        # (On-site)") establishes India alone; a card naming a foreign market
        # (Skylar: "Stamford, Connecticut, United States") rejects alone.
        # Unrecognised cards ("", "Remote", "Hybrid") fall through to text.
        card_geo = _card_india_decision(post.job_card_location)
        if card_geo == "not_india":
            india_relevance = "Not India"
            return self._create_invalid(
                post,
                f"Not India (job card location: {post.job_card_location.strip()})",
                scraped_at,
            )
        if card_geo == "india":
            has_india_city = True
            india_evidence = f"Job card location: {post.job_card_location.strip()}"

        # Check explicit India cities/country (letter-boundaried: "agra" must
        # not match "instagram" — baseline Kali FP, which Groq hard-rejected
        # as no-India; city hashtags like "#MumbaiJobs" still count).
        if not has_india_city:
            for city in INDIA_CITIES:
                if _match_india_city(text_norm, city):
                    has_india_city = True
                    india_evidence = f"Matched India location: {city}"
                    break

        # Check compensation
        for comp in INDIA_COMPENSATION:
            if comp in text_norm:
                has_india_comp = True
                if not india_evidence:
                    india_evidence = f"Matched Indian compensation: {comp}"
                break

        # Check remote
        if "remote" in text_norm or "wfh" in text_norm.split():
            has_remote = True

        # Check international
        for loc in NON_INDIA_LOCATIONS + NON_INDIA_COUNTRIES:
            # match word boundaries for international locations to avoid substring matches
            if re.search(rf"\b{re.escape(loc)}\b", text_norm):
                has_intl_loc = True
                break
        # Phase 16: GBP salaries / .co.uk contacts (baseline C&C spotlight).
        if not has_intl_loc:
            for pat in NON_INDIA_PATTERNS:
                if re.search(pat, text_norm):
                    has_intl_loc = True
                    break

        if has_india_city:
            if "global" in text_norm or "worldwide" in text_norm or has_intl_loc:
                india_relevance = "India + Global"
                market = "India + Global"
            elif has_remote:
                india_relevance = "Remote - India"
                market = "Remote - India"
            else:
                india_relevance = "India"
                market = "India"
        elif has_intl_loc:
            india_relevance = "Not India"
            return self._create_invalid(post, "Not India (International location without India context)", scraped_at)
        else:
            # Compensation signals (₹ / INR / LPA / CTC / lakhs) may SUPPORT
            # India relevance but are never sufficient proof of it on their
            # own; they do not override the role/hiring gates above.
            reason = "Unclear India relevance"
            if has_india_comp:
                reason += " (Indian compensation signal alone does not prove India context)"
            # Phase 16 last-resort evidence (each guarded, LLM still gates).
            # (a) author / card-company context via app.geo: Indian company
            #     form, city, or decisive marker in the AUTHOR name or card
            #     company (baseline FN-2 Kent: author "Kent Constructions
            #     Pvt. Ltd."). Deliberately NOT the post text: the text gate
            #     above already failed on it, and rescanning it here (e.g.
            #     geo's decisive LPA rule on Kali's bare "3LPA") would undo
            #     the compensation-alone and hashtag fixes.
            # (b) a known Indian employer named in the post (baseline FN-6:
            #     INDmoney Founder's Office Fellow — full-time, zero geo
            #     tokens, only the employer name anchors it to India).
            context_scan = " ".join([
                (post.author_name or "").lower(),
                (post.job_card_company or "").lower(),
            ]).strip()
            geo_ok, geo_why = _geo_is_india(context_scan) if context_scan else (False, "")
            if geo_ok:
                has_india_city = True
                india_relevance = "India"
                market = "India"
                india_evidence = f"India signal in author/card-company context: {geo_why}"
            else:
                known = next(
                    (c for c in KNOWN_INDIAN_COMPANIES
                     if re.search(rf"\b{re.escape(c)}\b", text_norm)),
                    "",
                )
                if known:
                    has_india_city = True
                    india_relevance = "India"
                    market = "India"
                    india_evidence = f"Known Indian employer named in post: {known}"
                else:
                    return self._create_invalid(post, reason, scraped_at)

        # ── Stage 5: Field Extraction ─────────────────────────────────────────
        # Exact Role
        # NOTE: LinkedIn text overwhelmingly uses the curly apostrophe (\u2019);
        # Stage 2 accepts both spellings, so this display regex must too.
        # Boundary-based extraction: capture the role keyword plus trailing
        # descriptor words up to natural boundaries (sentence end, location
        # prepositions, hashtags, numbers, etc.). No hard character truncation.
        exact_role = "Unclear"
        # Phase 17: data titles added as alternation AFTER the pinned FO/CoS
        # shapes (FO/CoS-only posts extract byte-identically to before; data
        # posts extract their advertised title, e.g. "Data Analyst",
        # "Senior Data Scientist", "Business Intelligence Analyst").
        role_kw = re.compile(
            r"(chief\s+of\s+staff|founder['\u2019]?s?\s+office|"
            r"founders?\s+office|founder['\u2019]?s?\s+associate|founder\s+associate|"
            r"office\s+of\s+the\s+founder|"
            r"data\s+scientist|data\s+science\s+associate|"
            r"data\s+science\s+specialist|data\s+science\s+engineer|"
            r"decision\s+scientist|"
            r"applied\s+(?:data\s+scientist|scientist)|"
            r"product\s+data\s+scientist|research\s+data\s+scientist|"
            r"machine\s+learning\s+(?:data\s+scientist|scientist)|"
            r"ml\s+scientist|junior\s+data\s+scientist|"
            r"associate\s+data\s+scientist|data\s+science\s+intern|"
            r"data\s+analyst|analytics\s+analyst|business\s+data\s+analyst|"
            r"bi\s+analyst|business\s+intelligence\s+analyst|"
            r"reporting\s+analyst|product\s+data\s+analyst|product\s+analyst|"
            r"growth\s+data\s+analyst|marketing\s+data\s+analyst|"
            r"operations\s+data\s+analyst|analytics\s+associate|"
            r"financial\s+data\s+analyst|finance\s+data\s+analyst|"
            r"risk\s+data\s+analyst|supply\s+chain\s+data\s+analyst|"
            r"customer\s+data\s+analyst|revenue\s+data\s+analyst|"
            r"commercial\s+data\s+analyst|data\s+analy(?:sis|tics)\s+analyst|"
            r"data\s+analytics\s+specialist|"
            r"junior\s+data\s+analyst|associate\s+data\s+analyst|"
            r"business\s+analyst)\b",
            re.IGNORECASE,
        )
        m = role_kw.search(post.text)
        if m:
            # Phase 18E: preserve a seniority prefix the keyword match starts
            # AFTER ("Senior Data Scientist", "Jr. Data Analyst"). Only a
            # closed seniority set qualifies, so prose ("Looking for a ...")
            # can never attach. Keywords that already include the seniority
            # ("junior data analyst") are unaffected — the lookbehind simply
            # finds no further prefix word.
            seniority_prefix = ""
            _pre = post.text[:m.start()].rstrip()
            _pm = re.search(
                r"(senior|sr\.?|junior|jr\.?|lead|principal|staff)\s*$",
                _pre, re.IGNORECASE)
            if _pm:
                seniority_prefix = _pm.group(1).strip()
            # Start from the keyword; keep only descriptor words up to a sentence
            # boundary, a location preposition, or a soft cap.
            tail = post.text[m.end():].strip()
            # Split at sentence boundary
            tail = re.split(r"(?<=[.;:!?])\s+|\.\s*$", tail, maxsplit=1)[0]
            # Drop trailing location / qualifier prepositions
            tail = re.split(
                r"(?:^|\s)(?:in|at|based\s+in|located\s+in|for|across|within)(?=\s|$)",
                tail, maxsplit=1)[0]
            tail_words = tail.split()
            kept = []
            for w in tail_words:
                if w in (",", ")", "(", "|", ":", ";") or w.startswith("#"):
                    break
                # Phase 18E: a URL (or bare shortlink host) is never part of
                # a job title — "Chief of Staff https://lnkd.in/..." (Karta
                # class) must stop at the title, not fuse the link/second role.
                if w.startswith("http") or "://" in w or w.lower().startswith(
                        ("lnkd.in", "forms.gle", "bit.ly", "tinyurl.com")):
                    break
                if re.fullmatch(r"\d+(?:\.\d+)?(?:-?\d+)?", w):
                    break
                if w.lower() in ("lpa", "inr", "ctc", "apply", "applynow", "please"):
                    break
                if not re.search(r"[A-Za-z0-9]", w):
                    # Emoji / lone punctuation (e.g. "📍" after "CEO OFFICE")
                    # ends the title — unless it is the FIRST token, where a
                    # dash-like separator belongs to the title shape
                    # ("Chief of Staff - India").
                    if kept:
                        break
                    if w not in ("-", "–", "—", "|", "/", ":"):
                        break
                kept.append(w)
                if len(kept) >= 8:   # soft cap on descriptor length
                    break
            # A leading "-" is a title separator (e.g. "Chief of Staff - India");
            # keep it, only strip a bare lone dash.
            if kept and kept[0] == "-" and len(kept) == 1:
                kept = []
            trailing = " ".join(kept).strip().strip(",:;")
            # Only accept trailing when it reads as part of a title, not a verb/sentence.
            if trailing and not re.match(r"^(?:is|are|we|apply|please|\d|hiring|join|and)", trailing, re.IGNORECASE):
                title = f"{m.group(1).strip()} {trailing}".strip()
            else:
                title = m.group(1).strip()
            if seniority_prefix:
                title = f"{seniority_prefix} {title}".strip()
            # Reasonable max length for a title (not a sentence)
            if len(title) > 120:
                title = title[:120].rstrip()
            if len(title) > 3:
                exact_role = title

        # Company Name — priority: "Join <X>" (employer self-identification,
        # beats "at <Name>" which also matches clients/platforms/cities) >
        # "<X> is/are hiring" > "at <X>" > "hiring <role> at <X>" >
        # "<X> hiring for" > job-card metadata. EVERY candidate passes through
        # clean_company_candidate (fail-closed): leading uppercase-led words
        # only, so IGNORECASE patterns can no longer yield junk like "re"
        # (from "you're hiring for") or "Tracktion where we work"; generic
        # descriptors ("Our Team", "a leading startup") are rejected too.
        # Baseline fixes: Enqurious (was "Fractal", a client named in-text —
        # "Join Enqurious," now wins over "at Fractal").
        company_name = "Unclear"
        _company_cands = [extract_join_company(post.text)]
        # Intra-name separators are [ \t], never \n: a company name never
        # spans a line break ("\s+" fused "MovieMe\n\nMovieMe" into one junk
        # name). The verb also covers "is looking for" (baseline Fat Pig:
        # "Fat Pig Ventures LLP is looking for ..."). Verbs match
        # case-insensitively ("Onextal is HIRING" resolves like
        # "Acme is hiring" — Phase 18C); the name itself stays uppercase-led
        # and validated, so behaviour on all-lowercase prose is unchanged.
        _is_hiring_m = extract_is_hiring_company(post.text)
        if _is_hiring_m:
            _company_cands.append(_is_hiring_m)
        # NOTE: \b on BOTH sides of at|join — without it the "at" inside
        # "Fat" ("Fat Pig Ventures LLP is looking ...") truncates the name
        # to "Pig Ventures LLP". "that Acme is hiring"-style matches are
        # still covered by the is-hiring pattern below (more precise anyway).
        _at_m = re.search(
            r"\b(?:at|join)\b ([A-Z][a-zA-Z0-9\s\&]+?)(?:\.|,|\n|$| hiring| is looking)",
            post.text,
        )
        if _at_m:
            _company_cands.append(_at_m.group(1))
        _hiring_at_m = re.search(
            r"\bhiring\s+(?:for\s+)?(?:[^.]{1,60}?\b)?at\s+([A-Z][A-Za-z0-9&\.\-]*(?:[ \t]+[A-Z][A-Za-z0-9&\.\-]*){0,3})\b",
            post.text,
            re.IGNORECASE,
        )
        if _hiring_at_m:
            _company_cands.append(_hiring_at_m.group(1))
        _hiring_for_m = re.search(
            r"\b([A-Z][A-Za-z0-9&\.\-]*(?:[ \t]+[A-Z][A-Za-z0-9&\.\-]*){0,3})\s+hiring\s+for\b",
            post.text,
            re.IGNORECASE,
        )
        if _hiring_for_m:
            _company_cands.append(_hiring_for_m.group(1))
        for _raw_cand in _company_cands:
            if not _raw_cand or len(_raw_cand) >= 40:
                continue
            _cleaned = clean_company_candidate(_raw_cand)
            # BUG A: a generic geographic / country / government term must never
            # be surfaced as the hiring company (e.g. "India is hiring..." from a
            # national jobs aggregator). Keep company "Unclear" instead.
            if _cleaned and _cleaned.lower() not in GENERIC_PLACEHOLDER_COMPANIES:
                company_name = _cleaned
                break
        if company_name == "Unclear" and post.company and post.company != "Unclear":
            # post.company is scraper metadata (job_card_company); treat generic
            # placeholders the same. This is a strong signal when present (~18% of posts).
            _meta = clean_company_candidate(post.company)
            if _meta and _meta.lower() not in GENERIC_PLACEHOLDER_COMPANIES:
                company_name = _meta

        # CTC
        ctc = "Not Disclosed"
        ctc_pattern = re.search(r"(₹?\d+[\-\s]?\d*\s*(?:LPA|lpa|Cr|cr|lakhs?|k|K)|CTC\s*[:=]?\s*₹?\d+)", post.text, re.IGNORECASE)
        if ctc_pattern:
            ctc = ctc_pattern.group(1).strip()
            
        # Location — city spellings canonicalised (Bangalore/Bengaluru are
        # one place) and bare states dropped whenever a city matched
        # ("Bhubaneswar, Odisha" -> "Bhubaneswar", not "Odisha / Bhubaneswar").
        _LOC_ALIAS = {"bangalore": "Bengaluru", "bombay": "Mumbai"}
        _LOC_STATES = {"karnataka", "maharashtra", "telangana", "tamil nadu",
                       "kerala", "gujarat", "rajasthan", "haryana",
                       "uttar pradesh", "west bengal", "andhra pradesh",
                       "madhya pradesh", "odisha", "assam", "chhattisgarh"}
        location = "Not Specified"
        matched_locs = []
        for city in INDIA_CITIES:
            if city not in ["india", "remote - india", "remote india"] \
                    and _match_india_city(text_norm, city):
                matched_locs.append(_LOC_ALIAS.get(city, city.title()))
        matched_locs = list(dict.fromkeys(matched_locs))  # de-dupe, keep order
        if len(matched_locs) > 1:
            _cities_only = [l for l in matched_locs if l.lower() not in _LOC_STATES]
            if _cities_only:
                matched_locs = _cities_only
        if matched_locs:
            location = " / ".join(list(set(matched_locs)))
        elif has_remote:
            location = "Remote"
        # Job-card location enriches the display when the text names no city
        # (card is authoritative geography; text extraction stays primary).
        if location in ("Not Specified", "Remote") and post.job_card_location:
            _card_city = next(
                (c for c in INDIA_CITIES
                 if c not in ("india", "remote - india", "remote india")
                 and _match_india_city(post.job_card_location.lower(), c)),
                "",
            )
            if _card_city:
                location = _LOC_ALIAS.get(_card_city, _card_city.title())

        # Experience
        experience = "Not Specified"
        exp_pattern = re.search(r"(\d+[\-\+]?\d*\s*(?:years?|yrs?|yr)|fresher|freshers welcome)", post.text, re.IGNORECASE)
        if exp_pattern:
            experience = exp_pattern.group(1).strip()

        # Cold Email (HIGH-PRIORITY, Phase 16): deterministic precedence over
        # the full literal address list — first company-domain address wins;
        # personal-domain mailboxes surface ONLY with explicit in-post
        # application-purpose framing; otherwise "Not Available" (never
        # fabricated from a company domain, never a bare personal address).
        email, _email_evidence = choose_cold_email(post.text)

        # Google Form + Apply Link (Phase 16): exact literal URLs only.
        # Shortlinks are never resolved; absent links stay "" (never invented).
        google_form_url = extract_google_form_url(post.text)
        apply_link = extract_apply_link(post.text, google_form_url)

        # Employment Type
        # Priority 1: job_card_employment_type from LinkedIn job card metadata.
        # These are authoritative structured sources when not "Unclear" or "job".
        # "job" is the generic LinkedIn actor field, NOT Full-time.
        if post.job_card_employment_type and post.job_card_employment_type not in ("Unclear", "job"):
            emp_type = post.job_card_employment_type
        else:
            # Priority 2: text-based detection on NORMALISED text, so
            # "Full‑Time" (non-breaking hyphen, baseline Dhairya FN-4:
            # "Engagement: Full‑Time") matches "full-time". Only explicit
            # values; anything else stays Unclear (never guessed).
            if "full-time" in text_norm or "full time" in text_norm or "permanent" in text_norm:
                emp_type = "Full-time"
            elif "contract" in text_norm:
                emp_type = "Contract"
            elif "part-time" in text_norm or "part time" in text_norm:
                emp_type = "Part-time"
            elif "internship" in text_norm:
                emp_type = "Internship"
            elif "freelance" in text_norm:
                emp_type = "Freelance"
            elif "temporary" in text_norm:
                emp_type = "Temporary"
            else:
                emp_type = "Unclear"

        # Hiring Manager (Phase 16): tiered, evidence-recorded, never inferred
        # from company-page authorship, never from seekers/reposts.
        #   Tier A: explicitly named founder/leader ("my co-founder Rishi Jain")
        #   Tier B: recruiter/TA signature block with contact ("Pooja Rani |
        #           Manager – Talent Acquisition" + phone/email)
        #   Tier C: first-person hiring evidence + PERSON author ("I'm hiring",
        #           "we're hiring", "we are seeking", "my/our team")
        # A company-page author (/company/ URL) is NEVER the manager; LinkedIn
        # URLs are only ever literal /in/ URLs from the post or the author's
        # own person-profile URL — never fabricated.
        hiring_manager_name = "Unclear"
        hiring_manager_linkedin = "Unclear"
        hiring_manager_evidence = ""
        _named_founder, _founder_role = extract_named_founder(post.text)
        _sig_name, _sig_title = extract_recruiter_signature(post.text)
        if _named_founder:
            hiring_manager_name = _named_founder
            hiring_manager_linkedin = "Unclear"
            hiring_manager_evidence = (
                f"named in post: {_named_founder}"
                + (f" ({_founder_role})" if _founder_role else "")
            )
        elif _sig_name:
            hiring_manager_name = _sig_name
            hiring_manager_linkedin = "Unclear"
            hiring_manager_evidence = f"recruiter signature in post: {_sig_name} ({_sig_title})"
        else:
            # First-person-SINGULAR ownership or own-team framing only. "We
            # are hiring/seeking" (company voice) is deliberately excluded:
            # the poster may be anyone sharing the company's vacancy (pinned
            # by test_author_not_auto_hiring_manager at the enrichment layer,
            # mirrored here). Plural seeking still counts as a hiring SIGNAL
            # and employer-CONTEXT upstream — just not a name attribution.
            hm_evidence = ["i'm hiring", "we're hiring", "i am hiring",
                           "my team", "our team"]
            if any(ev in text_norm for ev in hm_evidence):
                # BUG B: a LinkedIn URL containing "/company/" identifies a
                # COMPANY PAGE, never a person who is hiring. Only a person's
                # "/in/..." profile URL may surface the author as manager.
                if _is_person_profile_url(post.author_profile_url) and post.author_name:
                    hiring_manager_name = post.author_name
                    hiring_manager_linkedin = post.author_profile_url
                    _hit = next((ev for ev in hm_evidence if ev in text_norm), "")
                    hiring_manager_evidence = (
                        f"author states hiring side: '{_hit}'" if _hit else
                        "author states hiring side"
                    )

        # Description (Phase 16): FULL useful post text — the artificial
        # 403-char cap is removed. Whitespace-normalised, with Cold Email
        # addresses and Google Form URLs redacted (dedicated columns). No
        # truncation, no hallucination, no summarisation.
        description = build_description(post.text)
        # Key Points (Phase 16): max 4 extractive verbatim sentences (role /
        # hiring framing / company / location-req-comp). Never contains Cold
        # Email or form URLs; never invented.
        key_points = build_key_points(post.text, company_name)

        # ── Stage 6: Confidence Scoring ───────────────────────────────────────
        confidence = 0.60
        
        if min_distance <= 100:
            confidence += 0.15
        elif min_distance <= 300:
            confidence += 0.10
            
        if has_india_city:
            confidence += 0.10
            
        if company_name != "Unclear":
            confidence += 0.05
            
        if ctc != "Not Disclosed":
            confidence += 0.05
            
        if email != "Not Available" or "dm" in text_norm or "apply" in text_norm:
            confidence += 0.05
            
        if experience != "Not Specified":
            confidence += 0.02
            
        confidence = min(1.00, round(confidence, 2))

        # ── Stage 7: Final Acceptance Gate ────────────────────────────────────
        if confidence < CONFIDENCE_THRESHOLD:
            return self._create_invalid(post, f"Confidence {confidence} below threshold {CONFIDENCE_THRESHOLD}", scraped_at)

        return ClassifiedPost(
            **post.model_dump(),
            company_name=company_name,
            major_category=major_category,
            exact_role=exact_role,
            ctc=ctc,
            cold_email=email,
            hiring_manager_name=hiring_manager_name,
            hiring_manager_linkedin=hiring_manager_linkedin,
            hiring_manager_evidence=hiring_manager_evidence,
            apply_google_form=google_form_url,
            apply_link=apply_link,
            source_link=post.post_url,
            description=description,
            key_points=key_points,
            confidence=confidence,
            location=location,
            employment_type=emp_type,
            experience_requirement=experience,
            market=market,
            india_relevance=india_relevance,
            scraped_at=scraped_at,
            classification_reason="Passed all gates with explicit India relevance",
            status="New",
            matched_role_keywords=matched_role,
            hiring_intent_signals=matched_signal,
            india_evidence=india_evidence,
            is_valid=True
        )

    def _create_invalid(self, post: RawPost, reason: str, scraped_at: str) -> ClassifiedPost:
        return ClassifiedPost(
            **post.model_dump(),
            company_name="N/A",
            major_category="N/A",
            exact_role="N/A",
            ctc="N/A",
            cold_email="N/A",
            hiring_manager_name="N/A",
            hiring_manager_linkedin="N/A",
            source_link=post.post_url,
            description=post.text[:100].replace('\n', ' ') + "...",
            confidence=0.0,
            location="N/A",
            employment_type="N/A",
            experience_requirement="N/A",
            market="N/A",
            india_relevance="N/A",
            scraped_at=scraped_at,
            classification_reason=reason,
            status="N/A",
            matched_role_keywords="N/A",
            hiring_intent_signals="N/A",
            india_evidence="N/A",
            is_valid=False
        )
