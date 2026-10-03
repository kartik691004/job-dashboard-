#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
import importlib
import app.config
importlib.reload(app.config)
from app.config import EMPLOYER_RECRUITER_STRONG_PATTERNS, EMPLOYER_RECRUITER_SUPPORT_PATTERNS
import re

text = "Looking for a role in Founder's Office in Mumbai."
text_lower = text.lower()

print('Text:', text)
print()

strong_hit = any(re.search(p, text_lower, re.IGNORECASE) for p in EMPLOYER_RECRUITER_STRONG_PATTERNS)
print('Strong hit:', strong_hit)
if strong_hit:
    for p in EMPLOYER_RECRUITER_STRONG_PATTERNS:
        if re.search(p, text_lower, re.IGNORECASE):
            print('  STRONG:', p)

support_hit = any(re.search(p, text_lower, re.IGNORECASE) for p in EMPLOYER_RECRUITER_SUPPORT_PATTERNS)
print('Support hit:', support_hit)

def _is_employer_recruiter_context(text):
    strong_hit = any(re.search(p, text, re.IGNORECASE) for p in EMPLOYER_RECRUITER_STRONG_PATTERNS)
    if strong_hit:
        return True
    support_hit = any(re.search(p, text, re.IGNORECASE) for p in EMPLOYER_RECRUITER_SUPPORT_PATTERNS)
    if support_hit:
        if re.search(r"\b(?:interested|share|refer)\s+.*\bopening\b", text, re.IGNORECASE):
            return True
    return False

print('_is_employer_recruiter_context:', _is_employer_recruiter_context(text_lower))