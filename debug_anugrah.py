#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
import importlib
import app.config
importlib.reload(app.config)
import app.classifier
importlib.reload(app.classifier)
from app.classifier import DeterministicClassifier
from app.models import RawPost
import re
from app.config import (
    FOUNDERS_OFFICE_KEYWORDS, CHIEF_OF_STAFF_KEYWORDS, 
    FO_AMBIGUOUS_KEYWORD, FO_PROOF_PHRASES,
    EMPLOYER_RECRUITER_STRONG_PATTERNS, EMPLOYER_RECRUITER_SUPPORT_PATTERNS
)

text = """\u2728 Hiring Alert!
I'm looking for a Founder's Office Associate to work closely with me and the  team to help accelerate Dot - News, made new's growth and marketing.

You'll take ownership of Dot's day-to-day growth and presence across multiple channels, with a focus on:
Affiliate marketing
Community building
Content strategy
B2C customer acquisition & retention

This is a role where you'll have a lot of ownership and independence. It's fully remote and comes with a performance-linked, uncapped compensation structure.

It could be a great fit for students, aspirants, early-career candidates, or someone on a sabbatical who wants meaningful, hands-on experience working closely with a young, early-stage startup.

We'll initially start with a 3-6 month engagement, with the opportunity to extend based on performance and mutual fit.

If this sounds like you, or someone in your network, please apply through the link below!

https://lnkd.in/gTQQCnmU 

#Hiring #FoundersOffice #StartupJobs #GrowthMarketing #EarlyStageStartup #RemoteJobs"""

text_lower = text.lower()

# Stage 2: Role keyword detection
major_category = ''
matched_role = ''

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

print('Major category:', major_category)
print('Matched role:', matched_role)

# Check FO proof
if not major_category and FO_AMBIGUOUS_KEYWORD in text_lower.split():
    has_proof = any(proof in text_lower for proof in FO_PROOF_PHRASES)
    print('FO ambiguous with proof:', has_proof)

# Stage 3: Hiring intent - check job_seeking_pattern
role_term = matched_role.replace(' (with proof)', '')
job_seeking_pattern = re.compile(rf"(i am|i'm|i\u2019m) looking for a (.*?)({re.escape(role_term)})", re.IGNORECASE)
m = job_seeking_pattern.search(text_lower)
print('Job seeking pattern match:', m.group(0)[:80] if m else 'None')

# Check employer context
strong_hit = any(re.search(p, text_lower, re.IGNORECASE) for p in EMPLOYER_RECRUITER_STRONG_PATTERNS)
print('Strong hit:', strong_hit)

if strong_hit:
    for p in EMPLOYER_RECRUITER_STRONG_PATTERNS:
        if re.search(p, text_lower, re.IGNORECASE):
            print(f'  STRONG: {p}')

support_hit = any(re.search(p, text_lower, re.IGNORECASE) for p in EMPLOYER_RECRUITER_SUPPORT_PATTERNS)
print('Support hit:', support_hit)

# Check the extra condition for support
if support_hit:
    extra = re.search(r"\b(?:interested|share|refer)\s+.*\bopening\b", text_lower, re.IGNORECASE)
    print('Extra opening condition:', bool(extra))

# Now check _is_employer_recruiter_context function
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