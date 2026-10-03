#!/usr/bin/env python3
"""
Phase 15A Step 1: Fresh scrape and save raw dataset
"""
import os
import json
import time
from datetime import datetime

os.environ['DRY_RUN'] = 'True'

from app.sources.datadoping_source import DataDopingSource
from app.config import APIFY_API_TOKEN, SEARCH_QUERIES

def main():
    print("=" * 70)
    print("PHASE 15A: FRESH SCRAPE - RAW DATASET COLLECTION")
    print("=" * 70)
    print(f"Start time: {datetime.now().isoformat()}")
    
    source = DataDopingSource(APIFY_API_TOKEN)
    
    print(f"Running 19 queries with max 10 posts each...")
    raw_posts = source.search_all(SEARCH_QUERIES, max_posts=10)
    
    print(f"\nScraped {len(raw_posts)} raw posts")
    
    # Deduplicate
    unique_posts = []
    seen_urls = set()
    for p in raw_posts:
        if p.post_url not in seen_urls:
            seen_urls.add(p.post_url)
            unique_posts.append(p)
    
    print(f"Unique posts: {len(unique_posts)}")
    print(f"Duplicates removed: {len(raw_posts) - len(unique_posts)}")
    
    # Query breakdown
    query_counts = {}
    for q in SEARCH_QUERIES:
        query_counts[q] = 0
    for p in raw_posts:
        # We can't easily tell which query produced which post from the actor
        # since it runs all in one call. We'll just note the total per query from actor logs.
        pass
    
    # Save raw dataset
    raw_data = {
        "timestamp": datetime.now().isoformat(),
        "total_raw": len(raw_posts),
        "unique": len(unique_posts),
        "duplicates": len(raw_posts) - len(unique_posts),
        "queries": SEARCH_QUERIES,
        "posts": []
    }
    
    for p in unique_posts:
        raw_data["posts"].append({
            "post_url": p.post_url,
            "post_date": p.post_date,
            "text": p.text,
            "author_name": p.author_name,
            "author_profile_url": p.author_profile_url,
            "company": p.company,
            "job_card_location": p.job_card_location,
            "job_card_company": p.job_card_company,
            "job_card_employment_type": p.job_card_employment_type,
            "job_card_experience": p.job_card_experience,
        })
    
    output_path = f"data/validation/phase15_fresh_run/raw_dataset_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\nRaw dataset saved to: {output_path}")
    
    # Job card metadata summary
    job_card_count = sum(1 for p in unique_posts if p.job_card_location)
    job_card_company_count = sum(1 for p in unique_posts if p.job_card_company != "Unclear")
    job_card_emp_count = sum(1 for p in unique_posts if p.job_card_employment_type != "Unclear")
    job_card_exp_count = sum(1 for p in unique_posts if p.job_card_experience != "Unclear")
    
    print(f"\nJob Card Metadata:")
    print(f"  Posts with job_card_location: {job_card_count}")
    print(f"  Posts with job_card_company: {job_card_company_count}")
    print(f"  Posts with job_card_employment_type: {job_card_emp_count}")
    print(f"  Posts with job_card_experience: {job_card_exp_count}")
    
    return raw_data

if __name__ == "__main__":
    main()