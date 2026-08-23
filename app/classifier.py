import re
from typing import Tuple, Dict, Any
from datetime import datetime, timezone

from app.config import (
    FOUNDERS_OFFICE_KEYWORDS,
    CHIEF_OF_STAFF_KEYWORDS,
    FO_AMBIGUOUS_KEYWORD,
    FO_PROOF_PHRASES,
    HIRING_SIGNALS,
    EXCLUSIONS_JOB_SEEKER,
    EXCLUSIONS_CONGRATULATORY,
    EXCLUSIONS_INFORMATIONAL,
    INTERNSHIP_INDICATORS,
    INDIA_CITIES,
    NON_INDIA_LOCATIONS,
    NON_INDIA_COUNTRIES,
    INDIA_COMPENSATION,
    CONFIDENCE_THRESHOLD,
    PROXIMITY_CHAR_LIMIT,
)
from app.models import RawPost, ClassifiedPost

class DeterministicClassifier:
    def classify(self, post: RawPost) -> ClassifiedPost:
        text_lower = post.text.lower()
        scraped_at = datetime.now(timezone.utc).isoformat()
        
        # ── Stage 1: Exclusions ───────────────────────────────────────────────
        for ind in INTERNSHIP_INDICATORS:
            if ind in text_lower:
                return self._create_invalid(post, f"Internship detected: '{ind}'", scraped_at)
                
        for ex in EXCLUSIONS_JOB_SEEKER:
            if ex in text_lower:
                return self._create_invalid(post, f"Job-seeker post: '{ex}'", scraped_at)
                
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
                for sig in HIRING_SIGNALS:
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
             return self._create_invalid(post, "First-person job seeking pattern detected", scraped_at)


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
        elif has_india_comp:
            india_relevance = "Indian Company"
            market = "India"
        else:
            if has_intl_loc:
                india_relevance = "Not India"
                return self._create_invalid(post, "Not India (International location without India context)", scraped_at)
            else:
                india_relevance = "Unclear"
                return self._create_invalid(post, "Unclear India relevance", scraped_at)

        # ── Stage 5: Field Extraction ─────────────────────────────────────────
        # Exact Role
        exact_role = "Unclear"
        role_pattern = re.compile(r"(.{0,20})\b(chief of staff|founder's office|founders office|founder associate)\b(.{0,20})", re.IGNORECASE)
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
        if company_pattern and len(company_pattern.group(1)) < 30:
            company_name = company_pattern.group(1).strip()
        elif post.company and post.company != "Unclear":
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
            hiring_manager_name = post.author_name
            hiring_manager_linkedin = post.author_profile_url

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
