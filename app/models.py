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

class ClassifiedPost(RawPost):
    company_name: str = "Unclear"
    major_category: str
    exact_role: str = "Unclear"
    ctc: str = "Not Disclosed"
    cold_email: str = "Not Available"
    hiring_manager_name: str = "Unclear"
    hiring_manager_linkedin: str = "Unclear"
    source_link: str
    description: str = ""
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
