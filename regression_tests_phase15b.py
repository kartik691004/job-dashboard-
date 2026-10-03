#!/usr/bin/env python3
"""
Regression tests for Phase 15B deterministic hardening changes
"""
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

classifier = DeterministicClassifier()

def make_post(text, author="Test Author", author_url="https://linkedin.com/in/test"):
    return RawPost(
        post_url="https://test.com",
        post_date="2026-09-06T11:24:08.937000+00:00",
        text=text,
        author_name=author,
        author_profile_url=author_url,
        company="Unclear",
        job_card_location="",
        job_card_company="Unclear",
        job_card_employment_type="Unclear",
        job_card_experience="Unclear",
    )

print("=" * 60)
print("PHASE 15B REGRESSION TESTS")
print("=" * 60)

# Test 1: Aggregator patterns - "X fresh roles"
print("\n1. Testing aggregator patterns...")
aggregator_texts = [
    "Founder's Office & Chief of Staff Openings in India (Last 24 Hours)\n11 fresh roles for anyone looking to work directly with founders",
    "5 fresh roles for anyone looking to join startups in India",
    "Founder's Office roles in India - 10 fresh openings",
    "Chief of Staff openings in India - fresh roles for everyone",
]
for i, text in enumerate(aggregator_texts, 1):
    p = make_post(text, "Aggregator Test")
    c = classifier.classify(p)
    status = "REJECTED" if not c.is_valid else "PASSED (BAD)"
    print(f"  Test {i}: {status} - {c.classification_reason[:60]}")

# Test 2: Aggregator patterns - "roles in India" / "openings in India"
print("\n2. Testing 'roles in India' aggregator pattern...")
# This should NOT catch genuine single-employer posts
genuine_texts = [
    "We're hiring a Founder's Office Associate in Bengaluru. Apply now.",
    "Acme Corp is hiring a Chief of Staff in India. Apply now.",  # Clearer genuine post
]
for i, text in enumerate(genuine_texts, 1):
    p = make_post(text, "Genuine Test")
    c = classifier.classify(p)
    status = "PASSED" if c.is_valid else "REJECTED (BAD)"
    print(f"  Genuine {i}: {status} - {c.classification_reason[:60]}")

# Test 3: Aggregator - "fresh roles for anyone"
print("\n3. Testing 'fresh roles for anyone' pattern...")
p = make_post("11 fresh roles for anyone looking to work with founders", "Aggregator")
c = classifier.classify(p)
print(f"  Result: {'REJECTED' if not c.is_valid else 'PASSED'} - {c.classification_reason[:60]}")

# Test 4: Aggregator - "find here:" with link
print("\n4. Testing 'find here:' pattern...")
p = make_post("Great roles available. find here: https://lnkd.in/abc123", "Aggregator")
c = classifier.classify(p)
print(f"  Result: {'REJECTED' if not c.is_valid else 'PASSED'} - {c.classification_reason[:60]}")

# Test 5: Hiring Alert - bare headline should NOT be caught
print("\n5. Testing 'Hiring Alert' bare headline (should PASS)...")
p = make_post("Hiring Alert! We're hiring a Founder's Office Associate in Mumbai.", "Company")
c = classifier.classify(p)
print(f"  Result: {'PASSED' if c.is_valid else 'REJECTED (BAD)'} - {c.classification_reason[:60]}")

# Test 6: Hiring Alert - with aggregator language should be caught
print("\n6. Testing 'Hiring Alert' with aggregator language...")
p = make_post("Hiring Alert: 10 companies hiring this week for Founder's Office roles", "Job Board")
c = classifier.classify(p)
print(f"  Result: {'REJECTED' if not c.is_valid else 'PASSED (BAD)'} - {c.classification_reason[:60]}")

# Test 7: Job-seeker "Looking for a role" should be rejected
print("\n7. Testing job-seeker 'Looking for a role'...")
p = make_post("Looking for a role in Founder's Office in Mumbai.", "Job Seeker")
c = classifier.classify(p)
print(f"  Result: {'REJECTED' if not c.is_valid else 'PASSED (BAD)'} - {c.classification_reason[:60]}")

# Test 8: Employer "I'm looking for a Founder's Office Associate" should pass
print("\n8. Testing employer 'I'm looking for a Founder's Office Associate'...")
p = make_post("I'm looking for a Founder's Office Associate to join our team. We're hiring!", "Founder")
c = classifier.classify(p)
print(f"  Result: {'PASSED' if c.is_valid else 'REJECTED (BAD)'} - {c.classification_reason[:60]}")

# Test 9: Job-seeker "I am looking for a Chief of Staff role" should be rejected
print("\n9. Testing job-seeker 'I am looking for a Chief of Staff role'...")
p = make_post("I am looking for a Chief of Staff role in Bangalore.", "Job Seeker")
c = classifier.classify(p)
print(f"  Result: {'REJECTED' if not c.is_valid else 'PASSED (BAD)'} - {c.classification_reason[:60]}")

# Test 10: "for a role" in employer context should pass
print("\n10. Testing employer 'hiring for a role'...")
p = make_post("We are hiring for a role in Founder's Office at Acme Corp.", "Acme Corp")
c = classifier.classify(p)
print(f"  Result: {'PASSED' if c.is_valid else 'REJECTED (BAD)'} - {c.classification_reason[:60]}")

# Test 11: "for a role" in job-seeker context should be rejected
print("\n11. Testing job-seeker 'looking for a role'...")
p = make_post("I am looking for a role as Chief of Staff.", "Job Seeker")
c = classifier.classify(p)
print(f"  Result: {'REJECTED' if not c.is_valid else 'PASSED (BAD)'} - {c.classification_reason[:60]}")

# Test 12: Anugrah-like post (employer but remote without India)
print("\n12. Testing Anugrah-like post (employer, remote, no India)...")
p = make_post("Hiring Alert! I'm looking for a Founder's Office Associate to work with our team. Fully remote.", "Founder")
c = classifier.classify(p)
print(f"  Result: {'PASSED' if c.is_valid else 'REJECTED'} - {c.classification_reason[:60]}")

# Test 13: Elroy-like post (employer, explicit India)
print("\n13. Testing Elroy-like post (employer, explicit India)...")
p = make_post("Hiring: Founder's Office at Union Living! Mumbai (HQ), with occasional travel. Work in India.", "Founder")
c = classifier.classify(p)
print(f"  Result: {'PASSED' if c.is_valid else 'REJECTED (BAD)'} - {c.classification_reason[:60]}")
print(f"    Location: {c.location}, India relevance: {c.india_relevance}")

print("\n" + "=" * 60)
print("REGRESSION TESTS COMPLETE")
print("=" * 60)