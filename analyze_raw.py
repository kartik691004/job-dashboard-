#!/usr/bin/env python3
"""
Analyze raw data from fresh scrape
"""
import json
import sys

with open('data/validation/phase15_fresh_run/raw_dataset_20260907T030957.json', encoding='utf-8') as f:
    raw_data = json.load(f)

records = raw_data.get('posts', [])

print(f"Total unique posts: {len(records)}")
print()

# Job card metadata
job_card_loc = sum(1 for r in records if r.get('job_card_location'))
job_card_comp = sum(1 for r in records if r.get('job_card_company') != 'Unclear')
job_card_emp = sum(1 for r in records if r.get('job_card_employment_type') != 'Unclear')
job_card_exp = sum(1 for r in records if r.get('job_card_experience') != 'Unclear')

print(f"Job Card Metadata:")
print(f"  job_card_location: {job_card_loc}")
print(f"  job_card_company: {job_card_comp}")
print(f"  job_card_employment_type: {job_card_emp}")
print(f"  job_card_experience: {job_card_exp}")
print()

# Author profile URLs (company vs person)
company_authors = sum(1 for r in records if '/company/' in (r.get('author_profile_url') or ''))
person_authors = sum(1 for r in records if '/in/' in (r.get('author_profile_url') or ''))
no_author_url = sum(1 for r in records if not r.get('author_profile_url'))

print(f"Author Profile URLs:")
print(f"  Company pages (/company/): {company_authors}")
print(f"  Person profiles (/in/): {person_authors}")
print(f"  No URL: {no_author_url}")
print()

# Location analysis
locations = {}
for r in records:
    jc_loc = r.get('job_card_location', '')
    if jc_loc:
        locations[jc_loc] = locations.get(jc_loc, 0) + 1

print("Job Card Locations:")
for loc, count in sorted(locations.items(), key=lambda x: -x[1]):
    print(f"  {loc}: {count}")
print()

# Company analysis from job cards
companies = {}
for r in records:
    jc_comp = r.get('job_card_company', '')
    if jc_comp and jc_comp != 'Unclear':
        companies[jc_comp] = companies.get(jc_comp, 0) + 1

print("Job Card Companies:")
for comp, count in sorted(companies.items(), key=lambda x: -x[1]):
    print(f"  {comp}: {count}")
print()

# Sample text analysis - look for keywords
india_count = 0
pakistan_count = 0
international_count = 0
hiring_signals = 0
internship_signals = 0

for r in records:
    text = (r.get('text') or '').lower()
    jc_loc = (r.get('job_card_location') or '').lower()
    
    if 'pakistan' in text or 'karachi' in text or 'lahore' in jc_loc or 'islamabad' in jc_loc:
        pakistan_count += 1
    elif 'india' in text or 'bengaluru' in text or 'bangalore' in text or 'mumbai' in text or 'gurgaon' in text or 'delhi' in text or 'hyderabad' in text or 'chennai' in text or 'pune' in text:
        india_count += 1
    elif 'remote' in text or 'global' in text or 'worldwide' in text:
        international_count += 1
    
    if any(sig in text for sig in ['hiring', 'we are hiring', 'we\'re hiring', 'apply', 'job opening']):
        hiring_signals += 1
    
    if 'intern' in text:
        internship_signals += 1

print(f"Location Signals:")
print(f"  India signals: {india_count}")
print(f"  Pakistan signals: {pakistan_count}")
print(f"  International/Remote: {international_count}")
print()
print(f"Hiring signals detected: {hiring_signals}")
print(f"Internship signals detected: {internship_signals}")
print()

# List all posts with basic info
print("All Posts:")
for i, r in enumerate(records):
    text_preview = (r.get('text') or '')[:100].replace('\n', ' ')
    jc_loc = r.get('job_card_location', '')
    print(f"  {i+1}. {r.get('author_name', 'Unknown')} | {jc_loc} | {text_preview}...")