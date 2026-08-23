"""
migrate_schema.py — one-time migration of the LinkedIn Hiring Leads tab to the
FTB-mirroring column layout.

This is the only code allowed to rewrite that tab's header, and it is deliberately a
separate, explicitly-run script rather than something SheetsWriter does on startup:
restructuring a sheet silently is precisely the class of failure the writer's guards
exist to prevent.

Order of operations, so an interruption at any point is survivable:

  1. Refuse unless DRY_RUN=false, and refuse the protected gid=0 tab.
  2. Read the whole tab and write it to data/analysis/ as JSON. If a backup for this run
     cannot be written, stop before touching anything.
  3. Verify the header is the known legacy 15-column layout. Anything else stops.
  4. Map every row through app.schema.row_from_legacy and check the row count matches.
  5. Replace the tab contents in ONE batch update.
  6. Re-read and verify.

Nothing is deleted. The 6 rows matched only by the retired bare "generalist" keyword
(all of them HR Generalist / agency posts, none a target role) are kept and marked in
Status rather than removed, so the cleanup is visible and reversible in the sheet itself.
"""
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import gspread

from app.config import (
    DRY_RUN, GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID, GOOGLE_SHEET_WORKSHEET,
)
from app.schema import EXPECTED_HEADERS, LEGACY_HEADERS_V1, index_of, row_from_legacy

OUT = Path("data/analysis")

# Retired keyword. Rows whose only role match was this are flagged, not deleted.
RETIRED_KEYWORD = "generalist"
RETIRED_STATUS = "Needs review — matched retired 'generalist' keyword, not a target role"


def _only_retired_keyword(tags: str) -> bool:
    """True when every role keyword on the row is the retired bare 'generalist'.

    Tags is "<employment type>, <matched keywords>", so the employment type is dropped
    before testing. A row that also matched a real keyword is left alone.
    """
    toks = [t.strip().lower() for t in (tags or "").split(",") if t.strip()]
    toks = [t for t in toks if t not in ("full-time", "part-time", "internship")]
    return bool(toks) and all(t == RETIRED_KEYWORD for t in toks)


def main() -> None:
    print(f"DRY_RUN={DRY_RUN}")
    if DRY_RUN:
        print("Refusing to migrate during a dry run. Set DRY_RUN=false to proceed.")
        sys.exit(1)

    OUT.mkdir(parents=True, exist_ok=True)
    gc = gspread.service_account(filename=GOOGLE_SHEETS_CREDENTIALS)
    sheet = gc.open_by_key(GOOGLE_SHEETS_ID)
    ws = sheet.worksheet(GOOGLE_SHEET_WORKSHEET)

    print(f"Spreadsheet : {sheet.title}")
    print(f"Worksheet   : {ws.title} (gid={ws.id}, index={ws.index})")

    if ws.id == 0 or ws.index == 0:
        print("ABORT: resolved to the protected first tab (gid=0). Nothing touched.")
        sys.exit(1)

    values = ws.get_all_values()
    if not values:
        print("ABORT: worksheet is empty; nothing to migrate.")
        sys.exit(1)

    header, rows = values[0], values[1:]
    print(f"Header ({len(header)} cols) : {header}")
    print(f"Data rows                : {len(rows)}")

    # ── 2. Backup first, always ───────────────────────────────────────────────
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak = OUT / f"migration_backup_{stamp}.json"
    bak.write_text(json.dumps({
        "spreadsheet": sheet.title, "spreadsheet_id": GOOGLE_SHEETS_ID,
        "worksheet": ws.title, "gid": ws.id, "captured_at": stamp, "rows": values,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Backup written -> {bak}  ({bak.stat().st_size:,} bytes)")

    # ── 3. Only migrate a header we recognise ─────────────────────────────────
    if header == EXPECTED_HEADERS:
        print("Header already matches the current schema. Nothing to do.")
        return
    if header != LEGACY_HEADERS_V1:
        print("ABORT: header is neither the legacy 15-column layout nor the current one.")
        print(f"  Legacy   : {LEGACY_HEADERS_V1}")
        print(f"  Current  : {EXPECTED_HEADERS}")
        print("  Found    : (above). Nothing touched.")
        sys.exit(1)

    # ── 4. Map, then check nothing was lost ──────────────────────────────────
    non_empty = [r for r in rows if any(c.strip() for c in r)]
    migrated = [row_from_legacy(r) for r in non_empty]

    assert len(migrated) == len(non_empty), "row count changed during mapping"
    bad = [i for i, r in enumerate(migrated) if len(r) != len(EXPECTED_HEADERS)]
    if bad:
        print(f"ABORT: {len(bad)} mapped rows have the wrong width. Nothing touched.")
        sys.exit(1)

    # Post URL must survive the remap for dedup to keep working.
    src_urls = {r[0].strip() for r in non_empty if r and r[0].strip()}
    dst_col = index_of("Post URL") - 1
    dst_urls = {r[dst_col].strip() for r in migrated if r[dst_col].strip()}
    if src_urls != dst_urls:
        print(f"ABORT: Post URL set changed ({len(src_urls)} -> {len(dst_urls)}). Nothing touched.")
        sys.exit(1)
    print(f"Mapped {len(migrated)} rows; all {len(src_urls)} Post URLs preserved.")

    # Flag (never delete) the rows the retired keyword brought in.
    tags_col, status_col = index_of("Tags") - 1, index_of("Status") - 1
    flagged = 0
    for r in migrated:
        if _only_retired_keyword(r[tags_col]):
            r[status_col] = RETIRED_STATUS
            flagged += 1
    print(f"Flagged {flagged} rows as needing review (retired 'generalist' keyword). None deleted.")

    # ── 5. One batch replace ─────────────────────────────────────────────────
    body = [EXPECTED_HEADERS] + migrated
    needed_cols = len(EXPECTED_HEADERS)
    if ws.col_count < needed_cols:
        ws.add_cols(needed_cols - ws.col_count)
        print(f"Widened worksheet to {needed_cols} columns.")

    ws.clear()
    ws.update(values=body, range_name="A1", value_input_option="USER_ENTERED")
    print(f"Wrote {len(body)} rows ({len(migrated)} data + header).")

    # ── 6. Verify from the server, not from local state ───────────────────────
    check = ws.get_all_values()
    print()
    print("=" * 72)
    print("VERIFICATION")
    print("=" * 72)
    print(f"Header matches schema : {check[0] == EXPECTED_HEADERS}")
    print(f"Data rows             : {len(check) - 1} (expected {len(migrated)})")
    back = {r[dst_col].strip() for r in check[1:] if len(r) > dst_col and r[dst_col].strip()}
    print(f"Post URLs intact      : {back == src_urls} ({len(back)})")
    widths = sorted({len(r) for r in check[1:]})
    print(f"Row widths            : {widths}")
    print(f"Backup                : {bak}")


if __name__ == "__main__":
    main()
