#!/usr/bin/env python3
"""Smoke test for Groq provider with today's data."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import (
    LLM_PROVIDER, GROQ_API_KEY, GROQ_MODEL, GEMINI_API_KEY,
    GEMINI_MODEL, GROQ_TIMEOUT_S, GROQ_MAX_RETRIES,
)
from app.models import RawPost
from app.classifier import DeterministicClassifier
from app.llm.verifier import Verifier, enriched_post


def load_data():
    path = Path("data/validation/fresh_production_preview/fresh_preview.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def create_raw_post(record):
    return RawPost(
        post_url=record["source_link"],
        post_date=record.get("date", ""),
        text=record.get("text", ""),
        author_name=record.get("author", ""),
        author_profile_url="",
        company=record.get("company_meta", "Unclear"),
        job_card_location=record.get("job_card_location", ""),
    )


def main():
    print("=" * 70)
    print("PHASE 14A: GROQ PROVIDER SMOKE TEST")
    print("=" * 70)
    
    # Verify configuration
    print("\nConfiguration:")
    print(f"  LLM_PROVIDER: {LLM_PROVIDER}")
    print(f"  GROQ_API_KEY: {'SET (hidden)' if GROQ_API_KEY else 'NOT SET'}")
    print(f"  GROQ_MODEL: {GROQ_MODEL}")
    print(f"  GEMINI_API_KEY: {'SET (hidden)' if GEMINI_API_KEY else 'NOT SET'}")
    print(f"  GEMINI_MODEL: {GEMINI_MODEL}")
    
    # Load data
    data = load_data()
    
    # Get first 3 candidates that passed deterministic
    test_records = []
    for item in data.get("records", []):
        if item.get("stage") != "deterministic_REJECT" and len(test_records) < 3:
            test_records.append(item)
    
    print(f"\nTesting {len(test_records)} candidates...")
    
    # Initialize
    classifier = DeterministicClassifier()
    verifier = Verifier()
    
    # Verify provider
    provider_name = type(verifier.provider).__name__ if verifier.provider else "NONE"
    print(f"\nProvider instantiated: {provider_name}")
    print(f"Provider model: {verifier.provider.model if verifier.provider else 'N/A'}")
    
    if provider_name != "GroqProvider":
        print("FAIL: Expected GroqProvider but got " + provider_name)
        return False
    
    if verifier.provider.model != "openai/gpt-oss-120b":
        print(f"FAIL: Expected model 'openai/gpt-oss-120b' but got '{verifier.provider.model}'")
        return False
    
    print("OK Provider and model verified")
    
    # Test each candidate
    results = []
    for record in test_records:
        p = create_raw_post(record)
        c = classifier.classify(p)
        
        if not c.is_valid:
            print(f"\n{p.post_url} -> EXCLUDED by deterministic: {c.classification_reason}")
            results.append({"source_link": p.post_url, "decision": "EXCLUDED", "llm_called": False})
            continue
        
        print(f"\n{p.post_url}")
        print(f"  Deterministic: valid={c.is_valid}, company={c.company_name}, role={c.exact_role}")
        
        gate = verifier.verify(
            post_text=c.text,
            post_url=c.post_url,
            author_name=c.author_name,
            author_profile_url=c.author_profile_url,
            job_card_location=c.job_card_location,
        )
        
        merged = enriched_post(c, gate)
        
        results.append({
            "source_link": p.post_url,
            "llm_called": gate.llm_called,
            "decision": gate.decision,
            "confidence": gate.confidence,
            "reasons": gate.reasons,
            "llm_company": gate.verdict.company,
            "llm_exact_role": gate.verdict.exact_role,
            "llm_location": gate.verdict.location,
            "llm_employment": gate.verdict.employment_type,
            "llm_cold_email": gate.verdict.cold_email,
            "llm_hiring_mgr": gate.verdict.hiring_manager_name,
            "llm_hiring_mgr_linkedin": gate.verdict.hiring_manager_linkedin,
            "merged_company": merged.company_name,
            "merged_role": merged.exact_role,
            "merged_location": merged.location,
            "merged_employment": merged.employment_type,
            "merged_confidence": merged.confidence,
            "merged_status": merged.status,
        })
        
        print(f"  LLM decision: {gate.decision} (confidence={gate.confidence})")
        print(f"  Company: {gate.verdict.company}")
        print(f"  Role: {gate.verdict.exact_role}")
        print(f"  Location: {gate.verdict.location}")
        print(f"  Employment: {gate.verdict.employment_type}")
        print(f"  Reasons: {gate.reasons}")
    
    # Summary
    print("\n" + "=" * 70)
    print("SMOKE TEST SUMMARY")
    print("=" * 70)
    
    success = True
    for r in results:
        if r.get("llm_called"):
            if "429" in str(r.get("reasons", [])):
                print(f"FAIL {r['source_link']}: HTTP 429 rate limit")
                success = False
            elif "authentication" in str(r.get("reasons", [])).lower() or "unauthorized" in str(r.get("reasons", [])).lower():
                print(f"FAIL {r['source_link']}: Authentication error")
                success = False
            elif r.get("decision") == "REVIEW" and "unavailable" in str(r.get("reasons", [])).lower():
                print(f"FAIL {r['source_link']}: Provider unavailable fallback")
                success = False
            else:
                print(f"OK {r['source_link']}: {r['decision']} (confidence={r['confidence']})")
    
    if success:
        print("\nAll smoke tests PASSED")
    else:
        print("\nSome smoke tests FAILED")
    
    return success, results


if __name__ == "__main__":
    success, results = main()
    sys.exit(0 if success else 1)