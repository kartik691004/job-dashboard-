"""
filter_sheet_by_standards.py — re-judge every row on the `LinkedIn Hiring Leads`
tab against the CURRENT classifier (app.classifier.DeterministicClassifier).

Why this exists: the tab's 124 rows were accepted under older, looser rules. The
V3 strict classifier has since tightened — notably, Indian compensation signals
(Rs / INR / LPA / CTC / lakhs) no longer prove India context on their own. This
tool replays each stored Description through the live classifier and removes the
rows that would no longer pass. Kept rows are preserved byte-for-byte; only
failing rows disappear.

Safety (mirrors tools/migrate_to_txt_schema.py):
  1. Resolves the worksheet BY NAME and refuses gid=0 / index 0.
  2. Refuses to write unless DRY_RUN=false AND --live is passed.
  3. Backs the whole tab up to data/analysis/filter_backup_<ts>.json BEFORE
     any mutation.
  4. Only rewrites the tab when header == EXPECTED_HEADERS.
  5. One batch replace (RAW input), then re-read from the server to verify:
     kept-row count matches, and every surviving Source Link existed before.

Usage:
    python tools/filter_sheet_by_standards.py           # dry-run report
    python tools/filter_sheet_by_standards.py --live    # backup + rewrite
"""
import argparse
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gspread

from app.classifier import DeterministicClassifier
from app.config import (
    DRY_RUN,
    GOOGLE_SHEETS_CREDENTIALS,
    GOOGLE_SHEETS_ID,
    GOOGLE_SHEET_WORKSHEET,
)
from app.models import RawPost
from app.sheets_writer import EXPECTED_HEADERS

OUT = Path("data/analysis")


def row_to_rawpost(header: list, row: list) -> RawPost:
    """Rebuild the minimum RawPost the classifier needs from a sheet row."""
    d = dict(zip(header, list(row) + [""] * len(header)))
    return RawPost(
        post_url=d.get("Source Link", ""),
        post_date=d.get("Post Date", ""),
        text=d.get("Description", ""),
        author_name=d.get("Hiring Manager Name", ""),
        author_profile_url=d.get("Hiring Manager LinkedIn", ""),
        company=(d.get("Company Name") or "Unclear"),
    )


def filter_rows(values: list) -> tuple:
    """Classify every data row with the current standards.

    Returns (kept_rows, dropped) where dropped is a list of dicts:
    {row_number, source_link, company, reason}. values[0] must be the header.
    """
    header = values[0]
    classifier = DeterministicClassifier()
    kept, dropped = [list(header)], []
    for offset, row in enumerate(values[1:], start=2):
        if not any(c.strip() for c in row):
            continue  # blank padding row: neither kept nor reported
        d = dict(zip(header, list(row) + [""] * len(header)))
        result = classifier.classify(row_to_rawpost(header, row))
        if result.is_valid:
            kept.append(list(row))
        else:
            dropped.append({
                "row_number": offset,
                "source_link": d.get("Source Link", ""),
                "company": d.get("Company Name", ""),
                "reason": result.classification_reason,
            })
    return kept, dropped


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter the leads tab by current classification standards")
    parser.add_argument("--live", action="store_true",
                        help="Backup + rewrite the tab (still refused while DRY_RUN=true)")
    args = parser.parse_args()

    gc = gspread.service_account(filename=GOOGLE_SHEETS_CREDENTIALS)
    sheet = gc.open_by_key(GOOGLE_SHEETS_ID)
    ws = sheet.worksheet(GOOGLE_SHEET_WORKSHEET)

    print(f"Spreadsheet : {sheet.title}")
    print(f"Worksheet   : {ws.title} (gid={ws.id}, index={ws.index})")
    if ws.id == 0 or ws.index == 0:
        print("ABORT: resolved to the protected first tab (gid=0). Nothing touched.")
        sys.exit(1)

    values = ws.get_all_values()
    if not values or values[0] != EXPECTED_HEADERS:
        print("ABORT: header does not match EXPECTED_HEADERS; refusing to judge.")
        sys.exit(1)

    print(f"Data rows on tab: {len(values) - 1}")

    # Backup FIRST, always — even for a dry run, so the report is reproducible.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUT.mkdir(parents=True, exist_ok=True)
    bak = OUT / f"filter_backup_{stamp}.json"
    bak.write_text(json.dumps({
        "spreadsheet": sheet.title, "spreadsheet_id": GOOGLE_SHEETS_ID,
        "worksheet": ws.title, "gid": ws.id, "captured_at": stamp, "rows": values,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Backup written -> {bak}  ({bak.stat().st_size:,} bytes)")

    kept, dropped = filter_rows(values)

    print()
    print("=" * 72)
    print(f"KEEP {len(kept) - 1}   DROP {len(dropped)}   (of {len(values) - 1})")
    print("=" * 72)
    reasons: dict = {}
    for drop in dropped:
        base = drop["reason"].split(" (")[0]
        reasons[base] = reasons.get(base, 0) + 1
        print(f"  row {drop['row_number']:>4}  {drop['company'][:28]:<28} {drop['reason'][:70]}")
    if reasons:
        print("\nDrop reasons:")
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4} x {reason}")

    if not args.live:
        print("\nDRY RUN: nothing written. Re-run with --live (and DRY_RUN=false in .env)")
        print("to back up and remove the failing rows.")
        return

    if DRY_RUN:
        print("\nABORT: --live given but DRY_RUN=true in .env. Set DRY_RUN=false.")
        sys.exit(1)

    pre_links = {r[7].strip() for r in kept[1:] if len(r) > 7}
    body = kept
    needed_cols = len(EXPECTED_HEADERS)
    if ws.col_count < needed_cols:
        ws.add_cols(needed_cols - ws.col_count)
    ws.clear()
    ws.update(values=body, range_name="A1", value_input_option="RAW")
    print(f"Wrote {len(body)} rows ({len(body) - 1} data + header).")

    # Verify from the server, never from local state.
    check = ws.get_all_values()
    ok_header = check[0] == EXPECTED_HEADERS
    ok_count = len(check) - 1 == len(body) - 1
    post_links = {r[7].strip() for r in check[1:] if len(r) > 7}
    ok_links = post_links == pre_links
    print()
    print("VERIFICATION")
    print(f"Header intact          : {ok_header}")
    print(f"Row count              : {len(check) - 1} (expected {len(body) - 1})")
    print(f"Source Links exact set : {ok_links} ({len(post_links)})")
    if not (ok_header and ok_count and ok_links):
        print("VERIFICATION FAILED — restore from the backup JSON above.")
        sys.exit(2)


if __name__ == "__main__":
    # UTF-8 safe console output; only when run as a script so importing this
    # module (tests) does not rewrap pytest's captured stdout.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    main()
