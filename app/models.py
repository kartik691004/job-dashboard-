from pydantic import BaseModel
from typing import Optional

class RawPost(BaseModel):
    post_url: str
    post_date: str
    text: str
    author_name: str
    author_profile_url: str
    company: str = "Unclear"
    # Authoritative LinkedIn job-card location when the post carries one
    # ("Noida, Uttar Pradesh, India (On-site)"). Kept separate from
    # ClassifiedPost.location, which is the classifier's normalised output.
    job_card_location: str = ""
    # Job card company from "Job by <Company>" subtitle
    job_card_company: str = "Unclear"
    # Job card employment type (Full-time, Contract, Internship, etc.)
    job_card_employment_type: str = "Unclear"
    # Job card experience requirement if available
    job_card_experience: str = "Unclear"

class ClassifiedPost(RawPost):
    company_name: str = "Unclear"
    major_category: str
    exact_role: str = "Unclear"
    ctc: str = "Not Disclosed"
    cold_email: str = "Not Available"
    hiring_manager_name: str = "Unclear"
    hiring_manager_linkedin: str = "Unclear"
    # Phase 16: verbatim evidence backing the hiring-manager attribution
    # ("author states hiring side: 'i'm hiring'", "recruiter signature ...",
    # "named in post: ..."); "" when the manager is Unclear.
    hiring_manager_evidence: str = ""
    # Phase 16: exact literal Google Form application URL ("" when none —
    # never fabricated, shortlinks never resolved).
    apply_google_form: str = ""
    # Phase 16: best application URL (the Google Form when present, else a
    # URL with nearby application language; "" when none).
    apply_link: str = ""
    source_link: str
    description: str = ""
    # Phase 16: standardized extractive key points (max 4 verbatim lines).
    key_points: str = ""
    confidence: float
    location: str = "Not Specified"
    employment_type: str = "Unclear"
    experience_requirement: str = "Not Specified"
    market: str
    india_relevance: str = "Unclear"
    scraped_at: str
    classification_reason: str
    status: str = "New"
    
    # Internal fields for debugging and gating
    matched_role_keywords: str = ""
    hiring_intent_signals: str = ""
    india_evidence: str = ""
    is_valid: bool = False
