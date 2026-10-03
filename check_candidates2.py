#!/usr/bin/env python3
import json
import sys
sys.path.insert(0, '.')

import importlib
import app.config
importlib.reload(app.config)
import app.classifier
importlib.reload(app.classifier)
from app.classifier import DeterministicClassifier
from app.models import RawPost

with open('data/validation/phase15_fresh_run/raw_dataset_20260907T030957.json', encoding='utf-8') as f:
    data = json.load(f)

records = data.get('posts', [])
classifier = DeterministicClassifier()

# Check the three candidates
candidates = ['Elevatoz Loyalty', 'UNISON INTERNATIONAL', 'Elroy Fernandes']

for r in records:
    if r['author_name'] not in candidates:
        continue
    
    print(f"\n{'='*80}")
    print(f"AUTHOR: {r['author_name']}")
    print(f"URL: {r['post_url']}")
    
    p = RawPost(
        post_url=r['post_url'],
        post_date=r.get('post_date', ''),
        text=r.get('text', ''),
        author_name=r.get('author_name', ''),
        author_profile_url=r.get('author_profile_url', ''),
        company=r.get('company', 'Unclear'),
        job_card_location=r.get('job_card_location', ''),
        job_card_company=r.get('job_card_company', 'Unclear'),
        job_card_employment_type=r.get('job_card_employment_type', 'Unclear'),
        job_card_experience=r.get('job_card_experience', 'Unclear'),
    )
    
    c = classifier.classify(p)
    
    def safe(s):
        return s.encode('ascii', 'replace').decode('ascii')
    
    print(f"is_valid: {c.is_valid}")
    print(f"company_name: {safe(c.company_name)}")
    print(f"exact_role: {safe(c.exact_role)}")
    print(f"location: {safe(c.location)}")
    print(f"india_relevance: {safe(c.india_relevance)}")
    print(f"employment_type: {safe(c.employment_type)}")
    print(f"confidence: {c.confidence}")
    print(f"reason: {safe(c.classification_reason)}")
    
    text_lower = r['text'].lower()
    # Check for employment signals
    for sig in ['full-time', 'full time', 'permanent', 'contract', 'part-time', 'part time', 'freelance', 'temporary', 'internship', 'intern']:
        if sig in text_lower:
            idx = text_lower.index(sig)
            context = text_lower[max(0,idx-30):idx+30].encode('ascii', 'replace').decode('ascii')
            print(f"  Found '{sig}' at position {idx}: ...{context}...")
    
    # Check job card
    print(f"  Job card employment_type: {r.get('job_card_employment_type')}")
    print(f"  Job card experience: {r.get('job_card_experience')}")
    
    # Also show first 500 chars of text
    print(f"  Text preview: {safe(r['text'][:500])}")