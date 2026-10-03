#!/usr/bin/env python3
"""
Smoke test for new Groq API key
"""
import os
import sys
import time
import json

sys.path.insert(0, '.')

# Reload config to pick up new key
import importlib
import app.config
importlib.reload(app.config)
from app.llm.groq_provider import GroqProvider
from app.config import GROQ_API_KEY, GROQ_MODEL, GROQ_TIMEOUT_S, GROQ_MAX_RETRIES

def run_smoke_test(test_num, prompt, expected_in_response=None):
    print(f"\n{'='*60}")
    print(f"SMOKE TEST #{test_num}")
    print(f"{'='*60}")
    
    provider = GroqProvider(GROQ_API_KEY, GROQ_MODEL, GROQ_TIMEOUT_S, GROQ_MAX_RETRIES)
    print(f"Provider: {type(provider).__name__}")
    print(f"Model: {provider.model}")
    
    try:
        start = time.time()
        result = provider.complete_json(
            system="You are a helpful assistant. Return only valid JSON.",
            user=prompt
        )
        elapsed = time.time() - start
        
        print(f"SUCCESS (elapsed: {elapsed:.2f}s)")
        # Truncate response for display
        resp_str = json.dumps(result, indent=2)
        print(f"Response: {resp_str[:500]}...")
        
        if expected_in_response:
            if expected_in_response in str(result):
                print(f"[OK] Expected content '{expected_in_response}' found")
            else:
                print(f"[MISSING] Expected content '{expected_in_response}' NOT found")
        
        return {"success": True, "elapsed": elapsed, "response": result}
        
    except Exception as e:
        print(f"FAILURE: {type(e).__name__}: {e}")
        return {"success": False, "error": str(e), "elapsed": time.time() - start if 'start' in locals() else 0}

def main():
    print("=" * 60)
    print("GROQ NEW KEY SMOKE TEST")
    print("=" * 60)
    print(f"Model: {GROQ_MODEL}")
    print(f"Provider: groq")
    # Phase 22G: never log key material (not even prefix/suffix fragments).
    print(f"Key configured: {'yes' if GROQ_API_KEY else 'no'}")
    
    # Test 1: Simple JSON response
    print("\nWaiting 5 seconds before first request...")
    time.sleep(5)
    
    result1 = run_smoke_test(
        1,
        'Return JSON: {"test": "smoke1", "value": 42}',
        expected_in_response="smoke1"
    )
    
    # Test 2: More complex structured output
    print("\nWaiting 10 seconds before second request...")
    time.sleep(10)
    
    result2 = run_smoke_test(
        2,
        'Return JSON with fields: decision (ACCEPT/REVIEW/REJECT), confidence (0-1), reason (string). Example: {"decision": "ACCEPT", "confidence": 0.95, "reason": "test"}',
        expected_in_response="decision"
    )
    
    # Test 3: Realistic verification-like prompt
    print("\nWaiting 15 seconds before third request...")
    time.sleep(15)
    
    result3 = run_smoke_test(
        3,
        '''Evaluate this LinkedIn post and return JSON with: decision, confidence, reason.
Post: "We're hiring a Founder's Office Associate in Bengaluru. Apply today."
Return only JSON.''',
        expected_in_response="decision"
    )
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for i, r in enumerate([result1, result2, result3], 1):
        status = "PASS" if r["success"] else "FAIL"
        print(f"  Test {i}: {status} ({r.get('elapsed', 0):.2f}s)")
    
    all_passed = all(r["success"] for r in [result1, result2, result3])
    print(f"\nAll tests passed: {all_passed}")
    
    return all_passed

if __name__ == "__main__":
    main()