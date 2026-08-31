import re
from typing import Tuple, Dict, Any
from datetime import datetime, timezone

from app.config import (
    FOUNDERS_OFFICE_KEYWORDS,
    CHIEF_OF_STAFF_KEYWORDS,
    FO_AMBIGUOUS_KEYWORD,
    FO_PROOF_PHRASES,
    HIRING_SIGNALS,
    VACANCY_LABEL_SIGNALS,
    EXCLUSIONS_JOB_SEEKER,
    EXCLUSIONS_JOB_SEEKER_PATTERNS,
    EXCLUSIONS_CONGRATULATORY,
    EXCLUSIONS_INFORMATIONAL,
    INTERNSHIP_RE,
    INDIA_CITIES,
    NON_INDIA_LOCATIONS,
    NON_INDIA_COUNTRIES,
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
from app.models import RawPost, ClassifiedPost


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
    Matching is case-insensitive to handle title-case post headers.
    """
    return any(re.search(p, text, re.IGNORECASE) for p in ACTUAL_ROLE_STRONG_PATTERNS)


def _has_role_context_only(text):
    """True when the target keyword appears only as role-context / experience /
    preference (e.g. "high-ownership Founder's Office role", "Founder's Office
    experience preferred"), i.e. NOT a vacancy. Only consulted when strong
    actual-role evidence is absent."""
    return any(re.search(p, text) for p in ROLE_CONTEXT_WEAK_MARKERS)


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
        scraped_at = datetime.now(timezone.utc).isoformat()
        
        # ── Stage 1: Exclusions ───────────────────────────────────────────────
        # Phase-9E Bug 1: word-bounded internship detection (INTERNSHIP_RE)
        # replaces the former bare-substring check, so ordinary words like
        # "international", "interested", and "internet" no longer trigger a
        # false internship rejection, while genuine intern/intership/interning
        # mentions still reject.
        m = INTERNSHIP_RE.search(text_lower)
        if m:
            return self._create_invalid(
                post, f"Internship detected: '{m.group(0)}'", scraped_at
            )
        for ex in EXCLUSIONS_JOB_SEEKER:
            if ex in text_lower:
                return self._create_invalid(post, f"Job-seeker post: '{ex}'", scraped_at)
        for pat in EXCLUSIONS_JOB_SEEKER_PATTERNS:
            m = re.search(pat, text_lower, re.IGNORECASE)
            if m:
                return self._create_invalid(
                    post,
                    f"Job-seeker post: candidate seeking language '{m.group(0).strip()[:40]}'",
                    scraped_at,
                )
                
        for ex in EXCLUSIONS_CONGRATULATORY:
            if ex in text_lower:
                return self._create_invalid(post, f"Congratulatory/joining post: '{ex}'", scraped_at)
                
        for ex in EXCLUSIONS_INFORMATIONAL:
            if ex in text_lower:
                return self._create_invalid(post, f"Informational/discussion post: '{ex}'", scraped_at)

        # ── Stage 2: Role Keyword Detection ───────────────────────────────────
        major_category = ""
        matched_role = ""
        
        for keyword in FOUNDERS_OFFICE_KEYWORDS:
            if keyword in text_lower:
                major_category = "Founder's Office"
                matched_role = keyword
                break
                
        if not major_category:
            for keyword in CHIEF_OF_STAFF_KEYWORDS:
                if keyword in text_lower:
                    major_category = "Chief of Staff"
                    matched_role = keyword
                    break

        if not major_category:
            # Check ambiguous FO
            if FO_AMBIGUOUS_KEYWORD in text_lower.split():
                # Strict proof check
                has_proof = any(proof in text_lower for proof in FO_PROOF_PHRASES)
                if has_proof:
                    major_category = "Founder's Office"
                    matched_role = "fo (with proof)"
                else:
                    return self._create_invalid(post, "FO keyword found but no explicit Founder's Office proof phrase", scraped_at)
            else:
                return self._create_invalid(post, "No relevant role keyword found", scraped_at)

        # ── Stage 3: Hiring Intent Validation ─────────────────────────────────
        matched_signal = ""
        min_distance = float('inf')
        
        # We need to find the closest hiring signal to the matched role keyword
        # Since 'matched_role' might be "fo (with proof)", we look for the actual words
        search_roles = [matched_role] if matched_role != "fo (with proof)" else [FO_AMBIGUOUS_KEYWORD] + FO_PROOF_PHRASES

        for role_term in search_roles:
            if role_term not in text_lower: continue
            for role_match in re.finditer(re.escape(role_term), text_lower):
                role_start, role_end = role_match.span()
                # Phase 10: explicit vacancy labels (Role:/Position:/Title:/
                # Opening:/Vacancy:/Designation:/Join as) also satisfy the
                # proximity hint, alongside the active-hiring verbs. They never
                # widen which POST types pass (Stage 2 already requires a target
                # FO/CoS keyword, and Stages 3b/3c still reject aggregators,
                # context-only, and non-target roles). This only lets genuinely
                # label-framed vacancies reach the later gates.
                for sig in (*HIRING_SIGNALS, *VACANCY_LABEL_SIGNALS):
                    for sig_match in re.finditer(re.escape(sig), text_lower):
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
        if job_seeking_pattern.search(text_lower):
            # "I am looking for a Chief of Staff" can be either a JOB SEEKER
            # (wanting the role for themselves) or a RECRUITER/EMPLOYER (sourcing
            # a candidate for an organisation). Only reject as a job seeker when
            # there is NO strong employer/recruiter context; otherwise let the
            # post proceed through the normal hiring/role/India/full-time gates.
            if not _is_employer_recruiter_context(text_lower):
                return self._create_invalid(post, "First-person job seeking pattern detected", scraped_at)


        # ── Stage 3b: Single-employer / Aggregator validation (Phase 9A) ───────
        # A post that is a fixed list of jobs across MANY employers (roundups,
        # job alerts, curated lists, "batch of 40 roles", "X companies hiring")
        # is NOT a single-employer vacancy. It must be rejected BEFORE any field
        # extraction / enrichment, so an aggregator's company can never leak into
        # a lead. Two independent checks:
        #   (1) explicit aggregator phrasing;
        #   (2) >=2 distinct employers each "is/are hiring" (structural).
        for agg_re in AGGREGATOR_PATTERNS:
            if re.search(agg_re, text_lower):
                return self._create_invalid(
                    post,
                    f"Aggregator/roundup post detected: '{agg_re}'",
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
            if _has_role_context_only(text_lower):
                return self._create_invalid(
                    post,
                    "FO/CoS mentioned as role context/experience, not the advertised vacancy",
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
        
        # Check explicit India cities/country
        for city in INDIA_CITIES:
            if city in text_lower:
                has_india_city = True
                india_evidence = f"Matched India location: {city}"
                break
                
        # Check compensation
        for comp in INDIA_COMPENSATION:
            if comp in text_lower:
                has_india_comp = True
                if not india_evidence:
                    india_evidence = f"Matched Indian compensation: {comp}"
                break
                
        # Check remote
        if "remote" in text_lower or "wfh" in text_lower.split():
            has_remote = True
            
        # Check international
        for loc in NON_INDIA_LOCATIONS + NON_INDIA_COUNTRIES:
            # match word boundaries for international locations to avoid substring matches
            if re.search(rf"\b{re.escape(loc)}\b", text_lower):
                has_intl_loc = True
                break

        if has_india_city:
            if "global" in text_lower or "worldwide" in text_lower or has_intl_loc:
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
            return self._create_invalid(post, reason, scraped_at)

        # ── Stage 5: Field Extraction ─────────────────────────────────────────
        # Exact Role
        # NOTE: LinkedIn text overwhelmingly uses the curly apostrophe (\u2019);
        # Stage 2 accepts both spellings, so this display regex must too.
        exact_role = "Unclear"
        role_pattern = re.compile(
            r"(.{0,20})\b(chief of staff|founder['\u2019]?s office|founders office|founder['\u2019]?s associate|founder associate)\b(.{0,20})",
            re.IGNORECASE,
        )
        role_match = role_pattern.search(post.text)
        if role_match:
            exact_role = role_match.group(0).strip()
            # Clean up newlines and excessive spaces
            exact_role = re.sub(r'\s+', ' ', exact_role)
            if len(exact_role) > 60:
                exact_role = exact_role[:60] + "..."

        # Company Name
        company_name = "Unclear"
        company_pattern = re.search(r"(?:at|join) ([A-Z][a-zA-Z0-9\s\&]+?)(?:\.|,|\n|$| hiring| is looking)", post.text)
        if not company_pattern:
            # Fallback: "<Company> is/are hiring ..." — the most common phrasing
            # the primary regex misses. Pronoun subjects are excluded so
            # "We are hiring" never yields Company="We". Display only; scope of
            # target roles is unchanged.
            company_pattern = re.search(
                r"\b(?!(?:We|I|It|They|You|He|She)\b)([A-Z][A-Za-z0-9&\.\-]*(?:\s+[A-Z][A-Za-z0-9&\.\-]*){0,3})\s+(?:is|are)\s+hiring\b",
                post.text,
            )
        if company_pattern and len(company_pattern.group(1)) < 30:
            candidate = company_pattern.group(1).strip()
            # BUG A: a generic geographic / country / government term must never
            # be surfaced as the hiring company (e.g. "India is hiring..." from a
            # national jobs aggregator). Keep company "Unclear" instead.
            if candidate.lower() not in GENERIC_PLACEHOLDER_COMPANIES:
                company_name = candidate
        elif post.company and post.company != "Unclear":
            # post.company is scraper metadata; treat generic placeholders the same.
            if post.company.lower() not in GENERIC_PLACEHOLDER_COMPANIES:
                company_name = post.company

        # CTC
        ctc = "Not Disclosed"
        ctc_pattern = re.search(r"(₹?\d+[\-\s]?\d*\s*(?:LPA|lpa|Cr|cr|lakhs?|k|K)|CTC\s*[:=]?\s*₹?\d+)", post.text, re.IGNORECASE)
        if ctc_pattern:
            ctc = ctc_pattern.group(1).strip()
            
        # Location
        location = "Not Specified"
        matched_locs = []
        for city in INDIA_CITIES:
            if city in text_lower and city not in ["india", "remote - india", "remote india"]:
                matched_locs.append(city.title())
        if matched_locs:
            location = " / ".join(list(set(matched_locs)))
        elif has_remote:
            location = "Remote"

        # Experience
        experience = "Not Specified"
        exp_pattern = re.search(r"(\d+[\-\+]?\d*\s*(?:years?|yrs?|yr)|fresher|freshers welcome)", post.text, re.IGNORECASE)
        if exp_pattern:
            experience = exp_pattern.group(1).strip()

        # Email
        email = "Not Available"
        email_pattern = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", post.text)
        if email_pattern:
            email = email_pattern.group(0)

        # Employment Type
        emp_type = "Unclear"
        if "full-time" in text_lower or "full time" in text_lower or "permanent" in text_lower:
            emp_type = "Full-time"
        elif "contract" in text_lower:
            emp_type = "Contract"
            
        # Hiring Manager
        hiring_manager_name = "Unclear"
        hiring_manager_linkedin = "Unclear"
        # Only populate if evidence exists
        hm_evidence = ["i'm hiring", "we're hiring", "i am hiring", "my team", "our team"]
        hm_titles = ["founder", "ceo", "recruiter", "talent", "hr", "hiring manager"]
        
        is_hm = False
        if any(ev in text_lower for ev in hm_evidence):
            is_hm = True
        # we don't have author title, but if we did, we'd check it. We'll rely on text evidence.

        if is_hm:
            # BUG B: a LinkedIn URL containing "/company/" identifies a COMPANY
            # PAGE (e.g. ".../company/digitayal/posts"), never a person who is
            # hiring. Only a person's "/in/..." profile URL may be surfaced as
            # the hiring manager's own LinkedIn. If the author is a company
            # account, we keep the manager "Unclear" unless a separately-named
            # person is found later in enrichment. The company account may still
            # serve as evidence that this is a genuine hiring post.
            if _is_person_profile_url(post.author_profile_url):
                hiring_manager_name = post.author_name
                hiring_manager_linkedin = post.author_profile_url
            else:
                hiring_manager_name = "Unclear"
                hiring_manager_linkedin = "Unclear"

        # Description
        desc_clean = re.sub(r'\s+', ' ', post.text).strip()
        description = desc_clean[:400] + "..." if len(desc_clean) > 400 else desc_clean

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
            
        if email != "Not Available" or "dm" in text_lower or "apply" in text_lower:
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
            source_link=post.post_url,
            description=description,
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
