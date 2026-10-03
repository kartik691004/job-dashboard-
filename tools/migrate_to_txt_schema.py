"""
migrate_to_txt_schema.py — one-time migration of the `LinkedIn Hiring Leads` tab from
the legacy 15-column layout to the 19-column txt-spec layout that app.sheets_writer
writes (Company Name ... Status).

This supersedes tools/migrate_schema.py (FTB-mirror layout), which was abandoned when
the user directed the pipeline at the Founder's Office / Chief of Staff spec whose
columns live in app.sheets_writer.EXPECTED_HEADERS.

Order of operations, so an interruption at any point is survivable:

  1. Refuse unless DRY_RUN=false, and refuse the protected gid=0 tab.
  2. Read the whole tab and back it up to data/analysis/ as JSON BEFORE any mutation.
  3. Verify the header is the known legacy 15-column layout. Anything else stops.
  4. Map every row into the 19-column layout and assert nothing was lost.
  5. Replace the tab contents in ONE batch update (RAW input: no formula interpretation).
  6. Re-read from the server and verify.

Column mapping (legacy -> new):

  Company Name            <- Company
  Major Category          <- Role Category
  Exact Role              <- Detected Role Title (falls back to Role Category)
  CTC                     <- "Not Disclosed"      (never captured by the old schema)
  Cold Email              <- "Not Available"      (never captured)
  Hiring Manager Name     <- Author Name          (post author is the hiring-side contact)
  Hiring Manager LinkedIn <- Author Profile URL
  Source Link             <- Post URL             (dedup column, col 8)
  Description             <- Post Snippet
  Confidence Score        <- Confidence
  Location                <- recovered from Classification Reason India evidence,
                             else "Not Specified"
  Type                    <- Employment Type
  Experience Requirement  <- "Not Specified"      (never captured)
  Market                  <- "Remote - India" when remote evidence exists, else "India"
  India Relevance         <- "India"              (every legacy row passed the India gate)
  Post Date               <- Post Date
  Scraped At              <- Scraped At
  Classification Reason   <- Classification Reason
  Status                  <- Status

Nothing is deleted. Matched Role Keywords and Hiring Intent Signals have no home in the
new layout; they survive in the JSON backup, which is tracked in git.
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
from app.sheets_writer import EXPECTED_HEADERS

OUT = Path("data/analysis")

# The layout every row currently on the tab was written under.
LEGACY_HEADERS_V1 = [
    "Post URL", "Post Date", "Scraped At", "Role Category",
    "Employment Type", "Detected Role Title", "Company", "Author Name",
    "Author Profile URL", "Matched Role Keywords", "Hiring Intent Signals",
    "Confidence", "Post Snippet", "Classification Reason", "Status",
]

SOURCE_LINK_COL = EXPECTED_HEADERS.index("Source Link") + 1  # dedup reads col 8

_LOCATION_PATTERNS = [
    # app.geo evidence strings, written by the pipeline that scraped these rows.
    __import__("re").compile(r"India:\s*job location:\s*([^;]+)", __import__("re").I),
    __import__("re").compile(r"India:\s*India place name in text:\s*'([^']+)'", __import__("re").I),
    __import__("re").compile(r"Matched India location:\s*([^;,]+)", __import__("re").I),
]


def _location_from_reason(reason: str) -> str:
    for rx in _LOCATION_PATTERNS:
        m = rx.search(reason or "")
        if m:
            return m.group(1).strip().title()
    return ""


def _market(reason: str, snippet: str) -> str:
    blob = f"{reason} {snippet}".lower()
    if "remote" in blob or "wfh" in blob or "work from home" in blob:
        return "Remote - India"
    return "India"


def main() -> None:
    print(f"DRY_RUN={DRY_RUN}")
    if DRY_RUN:
        print("Refusing to migrate during a dry run. Set DRY_RUN=false to proceed.")
        sys.exit(1)

    OUT.mkdir(parents=True, exist_ok=True)
    from app.tls_trust import ensure_google_trust
    ensure_google_trust()  # Phase 28.4: OS-store trust before Google TLS
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

    # -- 2. Backup first, always ------------------------------------------------
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak = OUT / f"migration19_backup_{stamp}.json"
    bak.write_text(json.dumps({
        "spreadsheet": sheet.title, "spreadsheet_id": GOOGLE_SHEETS_ID,
        "worksheet": ws.title, "gid": ws.id, "captured_at": stamp, "rows": values,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Backup written -> {bak}  ({bak.stat().st_size:,} bytes)")

    # -- 3. Only migrate a header we recognise ----------------------------------
    if header == EXPECTED_HEADERS:
        print("Header already matches the 19-column txt-spec schema. Nothing to do.")
        return
    if header != LEGACY_HEADERS_V1:
        print("ABORT: header is neither the legacy 15-column layout nor the current one.")
        print(f"  Legacy   : {LEGACY_HEADERS_V1}")
        print(f"  Expected : {EXPECTED_HEADERS}")
        print("  Found    : (above). Nothing touched.")
        sys.exit(1)

    # -- 4. Map, then check nothing was lost ------------------------------------
    non_empty = [r for r in rows if any(c.strip() for c in r)]
    migrated = []
    for r in non_empty:
        g = dict(zip(LEGACY_HEADERS_V1, list(r) + [""] * len(LEGACY_HEADERS_V1)))
        title = g["Detected Role Title"]
        reason = g["Classification Reason"]
        snippet = g["Post Snippet"]
        loc = _location_from_reason(reason)
        try:
            conf = float(g["Confidence"]) if g["Confidence"].strip() else ""
        except ValueError:
            conf = g["Confidence"]
        migrated.append([
            g["Company"] or "Unclear",
            g["Role Category"],
            title if title.strip() and title != "Unclear" else (g["Role Category"] or "Unclear"),
            "Not Disclosed",
            "Not Available",
            g["Author Name"],
            g["Author Profile URL"].split("?")[0],
            g["Post URL"],
            snippet,
            conf,
            loc or "Not Specified",
            g["Employment Type"],
            "Not Specified",
            _market(reason, snippet),
            "India",
            g["Post Date"],
            g["Scraped At"],
            reason,
            g["Status"],
        ])

    assert len(migrated) == len(non_empty), "row count changed during mapping"
    bad = [i for i, r in enumerate(migrated) if len(r) != len(EXPECTED_HEADERS)]
    if bad:
        print(f"ABORT: {len(bad)} mapped rows have the wrong width. Nothing touched.")
        sys.exit(1)

    src_urls = {r[0].strip() for r in non_empty if r[0].strip()}
    dst_col = SOURCE_LINK_COL - 1
    dst_urls = {r[dst_col].strip() for r in migrated if r[dst_col].strip()}
    if src_urls != dst_urls:
        print(f"ABORT: Source Link set changed ({len(src_urls)} -> {len(dst_urls)}). Nothing touched.")
        sys.exit(1)
    print(f"Mapped {len(migrated)} rows; all {len(src_urls)} Post URLs preserved as Source Links.")

    located = sum(1 for r in migrated if r[EXPECTED_HEADERS.index("Location")] != "Not Specified")
    print(f"Location recovered from India evidence for {located}/{len(migrated)} rows.")

    # -- 5. One batch replace ----------------------------------------------------
    body = [EXPECTED_HEADERS] + migrated
    needed_cols = len(EXPECTED_HEADERS)
    if ws.col_count < needed_cols:
        ws.add_cols(needed_cols - ws.col_count)
        print(f"Widened worksheet to {needed_cols} columns.")

    ws.clear()
    # RAW: the formula-mangling scan found zero cells starting with =,+,-,@ but RAW can
    # never interpret one, so the migration cannot corrupt a snippet into #NAME?.
    ws.update(values=body, range_name="A1", value_input_option="RAW")
    print(f"Wrote {len(body)} rows ({len(migrated)} data + header).")

    # -- 6. Verify from the server, not from local state --------------------------
    check = ws.get_all_values()
    print()
    print("=" * 72)
    print("VERIFICATION")
    print("=" * 72)
    print(f"Header matches schema : {check[0] == EXPECTED_HEADERS}")
    print(f"Data rows             : {len(check) - 1} (expected {len(migrated)})")
    back = {r[dst_col].strip() for r in check[1:] if len(r) > dst_col and r[dst_col].strip()}
    print(f"Source Links intact   : {back == src_urls} ({len(back)})")
    widths = sorted({len(r) for r in check[1:]})
    print(f"Row widths            : {widths}")
    print(f"Backup                : {bak}")


if __name__ == "__main__":
    main()
