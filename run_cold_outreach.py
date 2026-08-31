"""
run_cold_outreach.py — manual entry point for the cold-mail step (app/outreach.py).

Reads the LinkedIn Hiring Leads tab, mails every Status=New row that carries a
real Cold Email address, and flips those rows to Status=Contacted.

Defaults to whatever DRY_RUN says in .env (true = read-only preview).
Pass --live to actually send:

    python run_cold_outreach.py            # dry-run preview
    python run_cold_outreach.py --live     # send + update Status cells
    python run_cold_outreach.py --live --max 5
"""
import argparse
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Cold-email new hiring leads")
    parser.add_argument("--live", action="store_true",
                        help="Actually send email and update the sheet "
                             "(overrides DRY_RUN for this process)")
    parser.add_argument("--max", type=int, default=None,
                        help="Max emails this invocation (default OUTREACH_MAX_PER_RUN)")
    args = parser.parse_args()

    # Must happen BEFORE app.config executes load_dotenv(), which does not
    # override variables already present in the environment.
    if args.live:
        os.environ["DRY_RUN"] = "false"

    from app.config import OUTREACH_MAX_PER_RUN
    from app.outreach import OutreachRunner

    summary = OutreachRunner().run(max_per_run=args.max if args.max is not None else OUTREACH_MAX_PER_RUN)
    print(json.dumps(summary, indent=2))

    if summary["dry_run"]:
        print("DRY RUN: nothing was sent or modified. Re-run with --live to send.")
        return
    if summary["failed"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
