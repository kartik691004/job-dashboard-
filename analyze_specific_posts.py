#!/usr/bin/env python3
"""
Analyze specific posts from fresh dataset to understand deterministic behavior
"""
import json
import sys
sys.path.insert(0, '.')

from app.classifier import DeterministicClassifier
from app.models import RawPost

with open('data/validation/phase15_fresh_run/raw_dataset_20260907T030957.json', encoding='utf-8') as f:
    raw_data = json.load(f)

records = raw_data.get('posts', [])
classifier = DeterministicClassifier()

# Key posts to analyze
key_posts = {
    # Aggregators that passed
    'Founders Office Roles': None,
    'High Finance Career India': None,
    # Elroy - proximity false negative
    'Elroy Fernandes': None,
    # UNISON - passed deterministic
    'UNISON INTERNATIONAL': None,
    # Anugrah - false internship trigger
    'Anugrah Agnihotri': None,
}

for r in records:
    if r['author_name'] in key_posts:
        key_posts[r['author_name']] = r

def safe_print(text):
    return text.encode('ascii', 'replace').decode('ascii')

for name, r in key_posts.items():
    if r is None:
        print(f"NOT FOUND: {name}")
        continue
    
    print(f"\n{'='*80}")
    print(f"AUTHOR: {name}")
    print(f"URL: {r['post_url']}")
    text_preview = safe_print(r['text'][:500])
    print(f"TEXT:\n{text_preview}...")
    print(f"{'='*80}")
    
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
    
    print(f"is_valid: {c.is_valid}")
    print(f"company_name: {safe_print(c.company_name)}")
    print(f"exact_role: {safe_print(c.exact_role)}")
    print(f"location: {safe_print(c.location)}")
    print(f"india_relevance: {safe_print(c.india_relevance)}")
    print(f"employment_type: {safe_print(c.employment_type)}")
    print(f"confidence: {c.confidence}")
    print(f"reason: {safe_print(c.classification_reason)}")
    print(f"matched_role_keywords: {safe_print(c.matched_role_keywords)}")
    print(f"hiring_intent_signals: {safe_print(c.hiring_intent_signals)}")
    print(f"india_evidence: {safe_print(c.india_evidence)}")
    
    # Check proximity
    text_lower = p.text.lower()
    role_term = c.matched_role_keywords.replace(' (with proof)', '')
    if role_term in text_lower:
        role_pos = text_lower.index(role_term)
        print(f"\nRole keyword '{role_term}' at position: {role_pos}")
    
    # Find hiring signals
    from app.config import HIRING_SIGNALS, VACANCY_LABEL_SIGNALS
    for sig in HIRING_SIGNALS:
        if sig in text_lower:
            pos = text_lower.index(sig)
            print(f"Hiring signal '{sig}' at position: {pos}, distance from role: {abs(pos - role_pos)}")
    for sig in VACANCY_LABEL_SIGNALS:
        if sig in text_lower:
            pos = text_lower.index(sig)
            print(f"Vacancy label '{sig}' at position: {pos}, distance from role: {abs(pos - role_pos)}")