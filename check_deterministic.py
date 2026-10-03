#!/usr/bin/env python3
"""
Check deterministic candidates from fresh raw data
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

valid_count = 0
for r in records:
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
    if c.is_valid:
        valid_count += 1
        role = c.exact_role.replace('\u2019', "'").replace('\u2018', "'").replace('\u201c', '"').replace('\u201d', '"')
        role = role.encode('ascii', 'replace').decode('ascii')
        company = c.company_name.encode('ascii', 'replace').decode('ascii')
        loc = c.location.encode('ascii', 'replace').decode('ascii')
        print(f'VALID: {company} | {role[:80]} | {loc} | {c.employment_type} | {c.india_relevance}')

print(f'Deterministic candidates: {valid_count}/{len(records)}')