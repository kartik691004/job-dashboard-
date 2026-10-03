#!/usr/bin/env python3
import json
import sys
sys.path.insert(0, '.')
from app.models import RawPost
from app.classifier import DeterministicClassifier

with open('data/validation/fresh_production_preview/fresh_preview.json', encoding='utf-8') as f:
    data = json.load(f)

records = data.get('records', [])
print('Total records:', len(records))

classifier = DeterministicClassifier()
valid_count = 0
for r in records:
    p = RawPost(
        post_url=r['source_link'],
        post_date=r.get('date', ''),
        text=r.get('text', ''),
        author_name=r.get('author', ''),
        author_profile_url='',
        company=r.get('company_meta', 'Unclear'),
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
        company = c.company_name.replace('\u2019', "'").replace('\u2018', "'").replace('\u201c', '"').replace('\u201d', '"')
        company = company.encode('ascii', 'replace').decode('ascii')
        loc = c.location.replace('\u2019', "'").replace('\u2018', "'").replace('\u201c', '"').replace('\u201d', '"')
        loc = loc.encode('ascii', 'replace').decode('ascii')
        emp = c.employment_type.encode('ascii', 'replace').decode('ascii')
        print('VALID:', company, '|', role[:80], '|', loc, '|', emp)

print('Deterministic candidates:', valid_count)