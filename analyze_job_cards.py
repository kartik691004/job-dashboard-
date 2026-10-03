#!/usr/bin/env python3
import json

with open('data/validation/phase15_fresh_run/raw_dataset_20260907T030957.json', encoding='utf-8') as f:
    data = json.load(f)

records = data.get('posts', [])
print('Total unique posts:', len(records))

# Job card analysis
job_card_count = 0
for r in records:
    if r.get('job_card_location'):
        job_card_count += 1
        author = r['author_name'][:30].encode('ascii', 'replace').decode('ascii')
        loc = r['job_card_location'][:40].encode('ascii', 'replace').decode('ascii')
        comp = r['job_card_company'][:30].encode('ascii', 'replace').decode('ascii')
        emp_type = r['job_card_employment_type']
        exp = r['job_card_experience']
        print(f'Author: {author:30s} | location: {loc:40s} | company: {comp:30s} | emp_type: {emp_type} | exp: {exp}')

print(f'Total with job_card_location: {job_card_count}')

# Query analysis - need to map posts to queries
# The actor doesn't tell us which query produced which post since it runs all in one call
# But we can look at the original 15A report for query-by-query breakdown

# Analyze employment types in text
print('\n--- Employment type signals in text ---')
emp_signals = ['full-time', 'full time', 'permanent', 'contract', 'part-time', 'part time', 'freelance', 'temporary', 'internship', 'intern']
for r in records:
    text_lower = (r.get('text') or '').lower()
    found = [sig for sig in emp_signals if sig in text_lower]
    if found:
        author = r['author_name'][:30].encode('ascii', 'replace').decode('ascii')
        print(f'Author: {author:30s} | signals: {found}')