#!/usr/bin/env python3
"""
Phase 15A: Fresh End-to-End Pipeline Quality Validation
Runs the full pipeline with current 19 queries and captures detailed results.
"""
import os
import json
import time
from datetime import datetime

os.environ['DRY_RUN'] = 'True'

from app.orchestrator import Orchestrator

def main():
    print("=" * 70)
    print("PHASE 15A: FRESH END-TO-END PIPELINE QUALITY VALIDATION")
    print("=" * 70)
    print(f"Start time: {datetime.now().isoformat()}")
    
    o = Orchestrator()
    
    # Run pipeline
    result = o.run_pipeline(limit=10)
    
    print("\n" + "=" * 70)
    print("PIPELINE RESULT")
    print("=" * 70)
    for k, v in result.items():
        print(f"  {k}: {v}")
    
    # Save detailed results
    output = {
        "timestamp": datetime.now().isoformat(),
        "pipeline_summary": result,
        "accepted_details": o._accepted_details,
        "review_details": o._review_details,
        "rejected_samples": o._rejected_samples,
        "enrichment_results": {k: v.model_dump() if hasattr(v, 'model_dump') else str(v) 
                               for k, v in o._enrichment_results.items()}
    }
    
    output_path = f"data/validation/phase15_fresh_run/phase15_fresh_run_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\nDetailed results saved to: {output_path}")
    
    # Also save raw dataset for reference
    raw_data_path = f"data/validation/phase15_fresh_run/raw_dataset_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    # We'd need to capture raw posts - let's do a separate scrape for that
    
    return result

if __name__ == "__main__":
    main()