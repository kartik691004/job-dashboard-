#!/usr/bin/env python3
import json

with open('data/validation/phase15_fresh_run/raw_dataset_20260907T030957.json', encoding='utf-8') as f:
    data = json.load(f)

records = data.get('posts', [])

# Find UNISON
for r in records:
    if 'UNISON' in r.get('author_name', ''):
        def safe(s):
            return s.encode('ascii', 'replace').decode('ascii')
        print(f"Found: {safe(r['author_name'])}")
        print(f"URL: {r['post_url']}")
        print(f"Text preview: {safe(r['text'][:500])}")
        print(f"Job card employment_type: {r.get('job_card_employment_type')}")
        print(f"Job card experience: {r.get('job_card_experience')}")
        break