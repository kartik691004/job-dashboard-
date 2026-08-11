"""
Standalone daily pipeline runner.
Runs the full funding & founder intelligence pipeline and exports Excel.

Usage:
    python run_daily.py
    python run_daily.py --limit 100
"""
import sys
import os
import argparse
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import validate_config
from src.pipeline.automation_runner import IndianStartupIntelligenceWorkflow


def main():
    parser = argparse.ArgumentParser(description="Run daily startup intelligence pipeline")
    parser.add_argument("--limit", type=int, default=None, help="Max startups to process")
    args = parser.parse_args()

    print("=" * 70)
    print("INDIAN STARTUP FUNDING & FOUNDER INTELLIGENCE — DAILY RUN")
    print("=" * 70)

    issues = validate_config()
    if issues:
        print("\nCONFIG ISSUES:")
        for issue in issues:
            print(f"  [WARN] {issue}")
        print()

    workflow = IndianStartupIntelligenceWorkflow()
    result = workflow.run_daily_workflow(limit=args.limit)

    print("\n" + "=" * 70)
    print("PIPELINE COMPLETE")
    print("=" * 70)
    for k, v in result.items():
        print(f"  {k}: {v}")
    print()


if __name__ == "__main__":
    main()
