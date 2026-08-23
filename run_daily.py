"""
run_daily.py — scheduled production runner for the LinkedIn Hiring Intelligence
pipeline.

Called by run_daily.bat from Windows Task Scheduler ("JobDashboard LinkedIn Pipeline",
Mon/Wed/Fri 01:00 IST). Three things it must handle itself, because nothing else will:

1. DRY_RUN: the scheduled run exists to write new leads, so this script forces
   DRY_RUN=false for its own process (before app.config loads) regardless of .env.
   To pause automation, disable the scheduled task instead:
       schtasks /Change /TN "JobDashboard LinkedIn Pipeline" /DISABLE

2. Apify budget: the FREE plan allows $5.00 per cycle starting the 11th. A run costs
   len(SEARCH_QUERIES) x max(limit, 10) x $0.00155 (~$0.30 at limit=10), so DAILY runs
   would exhaust the cap mid-cycle and silently return zero leads — which is why the
   task fires three times a week. This script still checks live spend before every run
   and refuses when the projected cost does not fit what remains.

3. Logging: stdout lands in logs\\scheduled_run.log via the .bat wrapper.
"""
import argparse
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# Must happen BEFORE app.config executes load_dotenv(), which does not override
# variables that already exist in the environment.
os.environ["DRY_RUN"] = "false"

from apify_client import ApifyClient

from app.config import APIFY_API_TOKEN, SEARCH_QUERIES
from app.orchestrator import PER_POST_USD

APIFY_FREE_CAP_USD = 5.0


def current_spend() -> float:
    """Cycle spend in USD. apify-client returns typed models: attribute access only."""
    client = ApifyClient(APIFY_API_TOKEN)
    return float(getattr(client.user().limits().current, "monthly_usage_usd", 0.0) or 0.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scheduled LinkedIn hiring-lead pipeline run")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max posts per keyword (actor floors at 10)")
    args = parser.parse_args()

    if not APIFY_API_TOKEN:
        print("ABORT: APIFY_API_TOKEN is not set.")
        sys.exit(1)

    projected = len(SEARCH_QUERIES) * max(args.limit, 10) * PER_POST_USD
    try:
        spent = current_spend()
    except Exception as e:
        print(f"WARN: could not read Apify spend ({e}); proceeding without budget check.")
        spent = 0.0
    remaining = APIFY_FREE_CAP_USD - spent

    print("=" * 72)
    print(f"Scheduled run @ {os.environ.get('COMPUTERNAME', '?')} — "
          f"{len(SEARCH_QUERIES)} keywords, limit={args.limit}")
    print(f"Apify spend this cycle : ${spent:.4f} of ${APIFY_FREE_CAP_USD:.2f}")
    print(f"Projected cost         : ~${projected:.2f} (remaining ${remaining:.4f})")
    print("=" * 72)

    if projected > remaining:
        print(f"ABORT: projected cost ${projected:.2f} exceeds the ${remaining:.4f} left "
              f"this cycle. Wait for the reset on the 11th or lower --limit.")
        sys.exit(1)

    # Imported here so the budget gate runs before any Sheets connection attempt.
    from app.orchestrator import Orchestrator

    summary = Orchestrator().run_pipeline(limit=args.limit)

    print(json.dumps(summary, indent=2))
    if summary.get("errors"):
        sys.exit(2)


if __name__ == "__main__":
    main()
