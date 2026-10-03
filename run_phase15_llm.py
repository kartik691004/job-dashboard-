#!/usr/bin/env python3
"""
Phase 15A: Run LLM verification on deterministic candidates with long delays
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
    print("PHASE 15A: LLM VERIFICATION ON DETERMINISTIC CANDIDATES")
    print("=" * 70)
    print(f"Start time: {datetime.now().isoformat()}")
    
    # Load fresh raw data
    raw_data = load_raw_data("data/validation/phase15_fresh_run/raw_dataset_20260907T030957.json")
    all_records = raw_data.get("posts", [])
    
    print(f"\nTotal unique records: {len(all_records)}")
    
    # Initialize
    classifier = DeterministicClassifier()
    verifier = Verifier()
    
    # Verify provider
    provider_name = type(verifier.provider).__name__ if verifier.provider else "NONE"
    print(f"Provider: {provider_name}")
    if hasattr(verifier.provider, 'model'):
        print(f"Model: {verifier.provider.model}")
    
    # First pass: deterministic classification
    print("\n--- Deterministic Classification ---")
    valid_posts = []
    classifier_results = []
    
    for i, record in enumerate(all_records):
        p = create_raw_post(record)
        c = classifier.classify(p)
        
        classifier_results.append({
            "source_link": p.post_url,
            "is_valid": c.is_valid,
            "company": c.company_name,
            "exact_role": c.exact_role,
            "location": c.location,
            "employment_type": c.employment_type,
            "india_relevance": c.india_relevance,
            "confidence": c.confidence,
            "reason": c.classification_reason,
        })
        
        if c.is_valid:
            valid_posts.append((p, c))
    
    print(f"Deterministic candidates: {len(valid_posts)}")
    for p, c in valid_posts:
        role = c.exact_role.encode('ascii', 'replace').decode('ascii')
        company = c.company_name.encode('ascii', 'replace').decode('ascii')
        loc = c.location.encode('ascii', 'replace').decode('ascii')
        print(f"  VALID: {company} | {role[:80]} | {loc} | {c.employment_type} | {c.india_relevance}")
    
    # Second pass: LLM verification with long delays
    print("\n--- LLM Verification (with 5s delays) ---")
    verifier_results = []
    
    for i, (p, c) in enumerate(valid_posts):
        print(f"  Processing {i+1}/{len(valid_posts)}...")
        
        # Long delay to avoid rate limits
        if i > 0:
            print(f"    Waiting 5 seconds to avoid rate limit...")
            time.sleep(5)
        
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
        print(f"    Decision: {gate.decision} (confidence: {gate.confidence})")
        if gate.reasons:
            print(f"    Reasons: {gate.reasons}")
    
    # Count results
    new_accept = sum(1 for r in verifier_results if r.get("decision") == "ACCEPT")
    new_review = sum(1 for r in verifier_results if r.get("decision") == "REVIEW")
    new_reject = sum(1 for r in verifier_results if r.get("decision") == "REJECT")
    deterministic_candidates = len(valid_posts)
    llm_calls = sum(1 for r in verifier_results if r.get('llm_called'))
    
    print(f"\nFinal Counts:")
    print(f"  Deterministic candidates: {deterministic_candidates}")
    print(f"  LLM calls: {llm_calls}")
    print(f"  ACCEPT: {new_accept}")
    print(f"  REVIEW: {new_review}")
    print(f"  REJECT: {new_reject}")
    
    # Save results
    output = {
        "timestamp": datetime.now().isoformat(),
        "pipeline_summary": {
            "scraped": raw_data["total_raw"],
            "unique": raw_data["unique"],
            "duplicates": raw_data["duplicates"],
            "deterministic_candidates": deterministic_candidates,
            "llm_calls": llm_calls,
            "accepted": new_accept,
            "review": new_review,
            "llm_rejected": new_reject,
            "excluded": len(all_records) - deterministic_candidates,
        },
        "classifier_results": classifier_results,
        "verifier_results": verifier_results,
    }
    
    output_path = f"data/validation/phase15_fresh_run/pipeline_results_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\nDetailed results saved to: {output_path}")

if __name__ == "__main__":
    main()