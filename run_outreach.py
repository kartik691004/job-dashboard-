import argparse
import sys
import logging
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.pipeline.outreach_workflow import OutreachWorkflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("OutreachRunner")

def main():
    parser = argparse.ArgumentParser(description="Run the Indian Startup B2B Outreach Automation")
    parser.add_argument(
        "--mode", 
        choices=["initial", "followup"], 
        required=True,
        help="Which workflow to run. 'initial' sends first touch to new startups, 'followup' sends day 3/7 followups."
    )
    
    args = parser.parse_args()
    
    workflow = OutreachWorkflow()
    
    if args.mode == "initial":
        logger.info("Starting WORKFLOW 2: Initial Outreach...")
        result = workflow.run_initial_outreach()
    elif args.mode == "followup":
        logger.info("Starting WORKFLOW 3: Followup Outreach...")
        result = workflow.run_followup_outreach()
    else:
        logger.error(f"Unknown mode: {args.mode}")
        sys.exit(1)
        
    logger.info("========================================")
    logger.info("OUTREACH RUN COMPLETE")
    logger.info(json.dumps(result, indent=2))
    logger.info("========================================")

if __name__ == "__main__":
    main()
