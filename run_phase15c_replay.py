#!/usr/bin/env python3
"""
Phase 15C: Controlled Replay with New Groq Key
Uses the exact 37-unique-post dataset from Phase 15A
"""
import os
import json
import time
import sys
from datetime import datetime

os.environ['DRY_RUN'] = 'True'

sys.path.insert(0, '.')

# Reload config to pick up new key
import importlib
import app.config
importlib.reload(app.config)
import app.classifier
importlib.reload(app.classifier)
import app.llm.verifier
importlib.reload(app.llm.verifier)

from app.classifier import DeterministicClassifier
from app.llm.verifier import Verifier, enriched_post
from app.models import RawPost

def load_phase15a_dataset(path):
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

def safe_print(text):
    return text.encode('ascii', 'replace').decode('ascii') if text else ''

def main():
    print("=" * 70)
    print("PHASE 15C: CONTROLLED REPLAY WITH NEW GROQ KEY")
    print("=" * 70)
    print(f"Start time: {datetime.now().isoformat()}")
    
    # Load Phase 15A dataset
    input_path = "data/validation/phase15_fresh_run/raw_dataset_20260907T030957.json"
    raw_data = load_phase15a_dataset(input_path)
    all_records = raw_data.get("posts", [])
    
    print(f"\nInput dataset: {input_path}")
    print(f"Total unique posts: {len(all_records)}")
    
    # Initialize
    classifier = DeterministicClassifier()
    verifier = Verifier()
    
    provider_name = type(verifier.provider).__name__ if verifier.provider else "NONE"
    print(f"Provider: {provider_name}")
    if hasattr(verifier.provider, 'model'):
        print(f"Model: {verifier.provider.model}")
    
    # Phase 1: Deterministic classification
    print("\n--- Phase 1: Deterministic Classification ---")
    deterministic_candidates = []
    excluded = []
    
    for i, record in enumerate(all_records):
        p = create_raw_post(record)
        c = classifier.classify(p)
        
        if c.is_valid:
            deterministic_candidates.append((p, c, record))
        else:
            excluded.append({
                "source_link": p.post_url,
                "author": p.author_name,
                "reason": c.classification_reason,
            })
    
    print(f"Deterministic candidates: {len(deterministic_candidates)}")
    print(f"Deterministic excluded: {len(excluded)}")
    
    for p, c, record in deterministic_candidates:
        role = safe_print(c.exact_role)[:60]
        company = safe_print(c.company_name)
        loc = safe_print(c.location)
        print(f"  CANDIDATE: {company} | {role} | {loc} | {c.india_relevance}")
    
    # Phase 2: LLM Verification with rate limiting
    print("\n--- Phase 2: LLM Verification (Groq) ---")
    verifier_results = []
    groq_calls = 0
    groq_errors = 0
    
    for i, (p, c, record) in enumerate(deterministic_candidates):
        print(f"\n  [{i+1}/{len(deterministic_candidates)}] Processing...")
        print(f"    Author: {safe_print(p.author_name)[:40]}")
        print(f"    Role: {safe_print(c.exact_role)[:60]}")
        
        # Rate limiting delay
        if i > 0:
            print(f"    Waiting 18 seconds for rate limit...")
            time.sleep(18)
        
        try:
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
            
            groq_calls += 1
            merged = enriched_post(c, gate)
            
            result = {
                "source_link": p.post_url,
                "author": safe_print(p.author_name),
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
                "rate_limit_info": {
                    "retry_after": getattr(gate.verdict, 'retry_after', None),
                    "x_ratelimit_remaining": getattr(gate.verdict, 'x_ratelimit_remaining', None),
                    "x_ratelimit_reset": getattr(gate.verdict, 'x_ratelimit_reset', None),
                }
            }
            verifier_results.append(result)
            
            print(f"    Decision: {gate.decision} (confidence: {gate.confidence})")
            if gate.reasons:
                print(f"    Reasons: {gate.reasons}")
                
        except Exception as e:
            groq_errors += 1
            print(f"    ERROR: {type(e).__name__}: {e}")
            verifier_results.append({
                "source_link": p.post_url,
                "author": safe_print(p.author_name),
                "llm_called": False,
                "decision": "ERROR",
                "error": str(e),
            })
    
    # Count results
    accept = sum(1 for r in verifier_results if r.get("decision") == "ACCEPT")
    review = sum(1 for r in verifier_results if r.get("decision") == "REVIEW")
    reject = sum(1 for r in verifier_results if r.get("decision") == "REJECT")
    error = sum(1 for r in verifier_results if r.get("decision") == "ERROR")
    
    print(f"\n{'='*70}")
    print("REPLAY SUMMARY")
    print(f"{'='*70}")
    print(f"Total unique posts: {len(all_records)}")
    print(f"Deterministic candidates: {len(deterministic_candidates)}")
    print(f"Groq calls: {groq_calls}")
    print(f"Groq errors: {groq_errors}")
    print(f"ACCEPT: {accept}")
    print(f"REVIEW: {review}")
    print(f"REJECT: {reject}")
    print(f"ERROR: {error}")
    print(f"Deterministic excluded: {len(excluded)}")
    
    # Exclusion reasons summary
    print(f"\nExclusion reasons:")
    reason_counts = {}
    for e in excluded:
        reason_counts[e['reason']] = reason_counts.get(e['reason'], 0) + 1
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"  {count}: {reason}")
    
    # Save detailed results
    output = {
        "timestamp": datetime.now().isoformat(),
        "input_dataset": input_path,
        "provider": provider_name,
        "model": verifier.provider.model if verifier.provider else None,
        "pipeline_summary": {
            "total_unique": len(all_records),
            "deterministic_candidates": len(deterministic_candidates),
            "groq_calls": groq_calls,
            "groq_errors": groq_errors,
            "accepted": accept,
            "review": review,
            "llm_rejected": reject,
            "error": error,
            "deterministic_excluded": len(excluded),
        },
        "excluded": excluded,
        "verifier_results": verifier_results,
        "phase15a_comparison": {
            "phase15a_groq_calls": 0,  # Was blocked by 429
            "phase15a_accepted": 0,
            "phase15a_review": 0,
            "phase15a_reject": 0,
            "phase15a_deterministic_candidates": 3,  # From Phase 15A
        }
    }
    
    output_path = f"data/validation/phase15_fresh_run/phase15c_replay_results_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\nDetailed results saved to: {output_path}")
    
    return output

if __name__ == "__main__":
    import sys
    main()