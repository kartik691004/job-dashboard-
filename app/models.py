from pydantic import BaseModel
from typing import Optional

class RawPost(BaseModel):
    post_url: str
    post_date: str
    text: str
    author_name: str
    author_profile_url: str
    company: str = "Unclear"

class ClassifiedPost(RawPost):
    role_category: str
    employment_type: str
    detected_role_title: str
    matched_role_keywords: str
    hiring_intent_signals: str
    confidence: float
    post_snippet: str
    classification_reason: str
    status: str = "New"
    is_valid: bool = False
