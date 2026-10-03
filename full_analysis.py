#!/usr/bin/env python3
"""
Full analysis of fresh dataset with updated classifier
"""
import json
import sys
sys.path.insert(0, '.')

# Reload modules
import importlib
import app.config
importlib.reload(app.config)
import app.classifier
importlib.reload(app.classifier)
from app.classifier import DeterministicClassifier
from app.models import RawPost

with open('data/validation/phase15_fresh_run/raw_dataset_20260907T030957.json', encoding='utf-8') as f:
    raw_data = json.load(f)

records = raw_data.get('posts', [])
classifier = DeterministicClassifier()

# Query mapping - we need to know which query each post came from
# Since the actor runs all queries in one call, we can't perfectly map
# But we can analyze overall

print("=" * 80)
print("PHASE 15B: FULL DATASET ANALYSIS WITH UPDATED CLASSIFIER")
print("=" * 80)

results = {
    'total': len(records),
    'valid': 0,
    'excluded': 0,
    'exclusion_reasons': {},
    'india_relevance': {},
    'employment_types': {},
    'international': 0,
    'india': 0,
    'aggregators_caught': 0,
    'job_seeker_caught': 0,
    'internship_caught': 0,
    'aggregator_fp': [],  # aggregators that passed (should be caught)
    'genuine_passed': [],
}

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
        results['valid'] += 1
        results['genuine_passed'].append({
            'author': r['author_name'],
            'company': c.company_name,
            'role': c.exact_role,
            'location': c.location,
            'india_relevance': c.india_relevance,
            'employment': c.employment_type,
            'reason': c.classification_reason,
        })
    else:
        results['excluded'] += 1
        reason = c.classification_reason
        results['exclusion_reasons'][reason] = results['exclusion_reasons'].get(reason, 0) + 1
        
        if 'Aggregator' in reason:
            results['aggregators_caught'] += 1
        elif 'Job-seeker' in reason:
            results['job_seeker_caught'] += 1
        elif 'Internship' in reason:
            results['internship_caught'] += 1
        elif 'Not India' in reason or 'international' in reason.lower():
            results['international'] += 1
        elif 'India relevance' in reason:
            results['international'] += 1

    # Track india relevance
    if c.india_relevance:
        results['india_relevance'][c.india_relevance] = results['india_relevance'].get(c.india_relevance, 0) + 1
    if c.employment_type:
        results['employment_types'][c.employment_type] = results['employment_types'].get(c.employment_type, 0) + 1

print(f"\nTotal unique posts: {results['total']}")
print(f"Deterministic candidates (passed): {results['valid']}")
print(f"Deterministic excluded: {results['excluded']}")
print(f"\nExclusion reasons:")
for reason, count in sorted(results['exclusion_reasons'].items(), key=lambda x: -x[1]):
    print(f"  {count}: {reason}")

print(f"\nIndia relevance distribution:")
for rel, count in sorted(results['india_relevance'].items(), key=lambda x: -x[1]):
    print(f"  {count}: {rel}")

print(f"\nEmployment types:")
for emp, count in sorted(results['employment_types'].items(), key=lambda x: -x[1]):
    print(f"  {count}: {emp}")

print(f"\nAggregators caught: {results['aggregators_caught']}")
print(f"Job-seeker caught: {results['job_seeker_caught']}")
print(f"Internship caught: {results['internship_caught']}")
print(f"International/No India: {results['international']}")

def safe(s):
    return s.encode('ascii', 'replace').decode('ascii')

print(f"\n--- Genuine candidates passed ({len(results['genuine_passed'])}) ---")
for g in results['genuine_passed']:
    print(f"  {safe(g['author'])[:30]:30s} | {safe(g['company'])[:25]:25s} | {safe(g['role'])[:35]:35s} | {safe(g['location'])[:20]:20s} | {safe(g['india_relevance'])}")

def safe(s):
    return s.encode('ascii', 'replace').decode('ascii')

# Check for aggregator false positives (should be caught but passed)
print(f"\n--- Checking for aggregator false positives ---")
for g in results['genuine_passed']:
    text = next((r['text'] for r in records if r['author_name'] == g['author']), '')
    text_lower = text.lower()
    aggregator_signals = [
        'fresh roles', 'roles in india', 'openings in india', 
        'roundup', 'curated', 'multiple companies', 'companies hiring',
        'here are', 'top jobs', 'top roles', 'find here:'
    ]
    for sig in aggregator_signals:
        if sig in text_lower:
            print(f"  POTENTIAL FP: {safe(g['author'])} - contains '{sig}'")

# Internship analysis
print(f"\n--- Internship analysis ---")
internship_posts = []
for r in records:
    text_lower = r['text'].lower()
    if 'intern' in text_lower:
        internship_posts.append(r)

print(f"Posts with 'intern' keyword: {len(internship_posts)}")
for r in internship_posts:
    c = classifier.classify(RawPost(
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
    ))
    status = "PASSED" if c.is_valid else "REJECTED"
    print(f"  {status}: {safe(r['author_name'])[:30]:30s} - {safe(r['text'])[:80]}...")

# Job card verification
print(f"\n--- Job card metadata verification ---")
for r in records:
    if r.get('job_card_location'):
        print(f"  {safe(r['author_name'])[:30]:30s} | loc: {safe(r['job_card_location'])[:40]:40s} | emp: {r['job_card_employment_type']} | exp: {r['job_card_experience']}")

# Duplicate analysis
print(f"\n--- Duplicate analysis ---")
print(f"Total raw: {raw_data['total_raw']}")
print(f"Unique: {raw_data['unique']}")
print(f"Duplicates: {raw_data['duplicates']} ({raw_data['duplicates']/raw_data['total_raw']*100:.1f}%)")

# Check which queries produce duplicates
# We can't perfectly map, but we can see if same URL appears multiple times
# (already done in raw_data)