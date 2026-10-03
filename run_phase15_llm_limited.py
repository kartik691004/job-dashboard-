#!/usr/bin/env python3
"""
Run LLM verification with VERY long delays (20s) - only 3 candidates max
"""
import os
import json
import time
from datetime import datetime

os.environ['DRY_RUN'] = 'True'

from app.classifier import DeterministicClassifier
from app.llm.verifier import Verifier, enriched_post
from app.models import RawPost

def load_raw_data(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def create_raw_post(record):
    return RawPost(
        post_url=record["post_url"],
        post_date=record.get("post_date", ""),
        text=record.get("text", ""),
        author_name=record.get("author_name", ""),
        author_profile_url=record.get("author_profile_url", ""),
        company=record.get("company", "Unclear"),
        job_card_location=record.get("job_card_location", ""),
        job_card_company=record.get("job_card_company", "Unclear"),
        job_card_employment_type=record.get("job_card_employment_type", "Unclear"),
        job_card_experience=record.get("job_card_experience", "Unclear"),
    )

def main():
    print("=" * 70)
    print("PHASE 15A: LLM VERIFICATION - LIMITED RUN")
    print("=" * 70)
    print(f"Start time: {datetime.now().isoformat()}")
    
    raw_data = load_raw_data("data/validation/phase15_fresh_run/raw_dataset_20260907T030957.json")
    all_records = raw_data.get("posts", [])
    
    classifier = DeterministicClassifier()
    verifier = Verifier()
    
    provider_name = type(verifier.provider).__name__ if verifier.provider else "NONE"
    print(f"Provider: {provider_name}")
    if hasattr(verifier.provider, 'model'):
        print(f"Model: {verifier.provider.model}")
    
    # Find deterministic candidates
    valid_posts = []
    for record in all_records:
        p = create_raw_post(record)
        c = classifier.classify(p)
        if c.is_valid:
            valid_posts.append((p, c))
    
    print(f"Deterministic candidates: {len(valid_posts)}")
    
    # Only process first 3 with 20s delays
    verifier_results = []
    for i, (p, c) in enumerate(valid_posts[:3]):
        print(f"Processing {i+1}/{min(3, len(valid_posts))}...")
        if i > 0:
            print(f"  Waiting 20 seconds...")
            time.sleep(20)
        
        gate = verifier.verify(
            post_text=c.text,
            post_url=c.post_url,
            author_name=c.author_name,
            author_profile_url=c.author_profile_url,
            job_card_location=c.job_card_location,
            job_card_company=c.job_card_company,
            job_card_employment_type=c.job_card_employment_type,
            job_card_experience=c.job_card_experience,
        )
        
        merged = enriched_post(c, gate)
        
        result = {
            "source_link": p.post_url,
            "llm_called": gate.llm_called,
            "decision": gate.decision,
            "status": gate.status,
            "confidence": gate.confidence,
            "reasons": gate.reasons,
            "llm_company": gate.verdict.company,
            "llm_exact_role": gate.verdict.exact_role,
            "llm_location": gate.verdict.location,
            "llm_employment": gate.verdict.employment_type,
            "llm_ctc": gate.verdict.ctc,
            "llm_cold_email": gate.verdict.cold_email,
            "llm_hiring_mgr": gate.verdict.hiring_manager_name,
            "llm_hiring_mgr_linkedin": gate.verdict.hiring_manager_linkedin,
            "llm_actual_role": gate.verdict.actual_role,
            "llm_major_category": gate.verdict.major_category,
            "llm_india_relevance": gate.verdict.india_relevance,
            "merged_company": merged.company_name,
            "merged_role": merged.exact_role,
            "merged_location": merged.location,
            "merged_employment": merged.employment_type,
            "merged_confidence": merged.confidence,
            "merged_status": merged.status,
            "merged_hiring_manager_name": merged.hiring_manager_name,
            "merged_hiring_manager_linkedin": merged.hiring_manager_linkedin,
            "merged_cold_email": merged.cold_email,
        }
        verifier_results.append(result)
        print(f"  Decision: {gate.decision} (confidence: {gate.confidence})")
        if gate.reasons:
            print(f"  Reasons: {gate.reasons}")
    
    new_accept = sum(1 for r in verifier_results if r.get("decision") == "ACCEPT")
    new_review = sum(1 for r in verifier_results if r.get("decision") == "REVIEW")
    new_reject = sum(1 for r in verifier_results if r.get("decision") == "REJECT")
    
    print(f"\nPartial Results (first {len(verifier_results)} candidates):")
    print(f"  ACCEPT: {new_accept}")
    print(f"  REVIEW: {new_review}")
    print(f"  REJECT: {new_reject}")
    
    # Save partial results
    output = {
        "timestamp": datetime.now().isoformat(),
        "note": "Partial results - only first 3 deterministic candidates processed due to Groq rate limits",
        "pipeline_summary": {
            "scraped": raw_data["total_raw"],
            "unique": raw_data["unique"],
            "duplicates": raw_data["duplicates"],
            "deterministic_candidates": len(valid_posts),
            "llm_calls": len(verifier_results),
            "accepted": new_accept,
            "review": new_review,
            "llm_rejected": new_reject,
            "excluded": len(all_records) - len(valid_posts),
        },
        "verifier_results": verifier_results,
    }
    
    output_path = f"data/validation/phase15_fresh_run/pipeline_results_partial_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"Partial results saved to: {output_path}")

if __name__ == "__main__":
    main()