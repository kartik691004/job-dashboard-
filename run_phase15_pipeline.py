#!/usr/bin/env python3
"""
Phase 15A: Full Pipeline on Fresh Data
"""
import os
import json
import time
from datetime import datetime

os.environ['DRY_RUN'] = 'True'

from app.orchestrator import Orchestrator

def main():
    print("=" * 70)
    print("PHASE 15A: FULL PIPELINE ON FRESH DATA")
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
        "enrichment_results": {}
    }
    
    for k, v in o._enrichment_results.items():
        if hasattr(v, 'model_dump'):
            output["enrichment_results"][k] = v.model_dump()
        else:
            output["enrichment_results"][k] = str(v)
    
    output_path = f"data/validation/phase15_fresh_run/pipeline_results_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\nDetailed results saved to: {output_path}")
    
    return result, output

if __name__ == "__main__":
    main()