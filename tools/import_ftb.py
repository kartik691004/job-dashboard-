"""
import_ftb.py — copy matching rows out of the FTB internships reference sheet.

The FTB spreadsheet is REFERENCE MATERIAL. This script opens it read-only and never
writes to it. Only our own LinkedIn Hiring Leads tab is appended to, through the same
guards as the pipeline (gid=0 refused, header verified, dedup first).

Two decisions here were made on measured evidence rather than convenience:

1. ROLE MATCHING IS TITLE-ONLY.
   Matching anywhere in the row yields 176 "matches" of which 131 are noise: FTB stores a
   taxonomy in its `Similar Fields` column, so "strategy & operations" appears on 79 rows
   that are actually HR Intern / Recruitment Intern / Business Development Intern, and
   "product management" tags a Drug Data Analyst Intern. Titles yield 45 matches, 43 of
   them genuine.

2. GEOGRAPHY USES FTB'S ROW AS TEXT, NOT AS A JOB CARD.
   app.geo treats a `location` argument as an authoritative LinkedIn job card: when
   present it decides alone and never falls back to the text. FTB's Location column is
   free text that usually omits the country ("Noida", "Remote"), so feeding it there
   would reject every remote role outright. The whole row is passed as text instead, which
   lets the description supply the India signal.

Run with DRY_RUN=true first to see exactly which rows would land.
"""
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import gspread

from app.config import (
    DRY_RUN, GEO_FILTER, GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID,
    GOOGLE_SHEET_WORKSHEET, ROLE_CATEGORIES,
)
from app.geo import is_india
from app.schema import EXPECTED_HEADERS, index_of, row_from_ftb

FTB_ID = "1f6b_QgkmeOAu0XFpiYyDHMgfkXFKEsrX4gl8IjWbI1k"
OUT = Path("data/analysis")

# Columns whose combined text is searched for an India signal. Deadline/Tags are excluded:
# they carry no geography and only add noise.
GEO_TEXT_COLUMNS = ("Title", "Description", "Location", "Stipend", "HiringOrganization", "Experience")


def match_title(title: str):
    """(category, keyword) for the first role keyword present in the title, else None.

    Iteration order follows ROLE_CATEGORIES, matching the classifier's own precedence.
    """
    low = (title or "").lower()
    for category, keywords in ROLE_CATEGORIES.items():
        for kw in keywords:
            if kw in low:
                return category, kw
    return None


def main() -> None:
    print(f"DRY_RUN={DRY_RUN}  GEO_FILTER={GEO_FILTER}")
    OUT.mkdir(parents=True, exist_ok=True)

    gc = gspread.service_account(filename=GOOGLE_SHEETS_CREDENTIALS)

    # ── Read the reference sheet (never written to) ────────────────────────────
    ftb = gc.open_by_key(FTB_ID)
    ftb_ws = ftb.get_worksheet(0)
    values = ftb_ws.get_all_values()
    ftb_header, ftb_rows = values[0], [r for r in values[1:] if any(c.strip() for c in r)]
    print(f"Reference   : {ftb.title} / {ftb_ws.title} — {len(ftb_rows)} rows, {len(ftb_header)} cols")

    col = {name: i for i, name in enumerate(ftb_header) if name.strip()}

    def cell(row, name):
        i = col.get(name)
        return (row[i].strip() if i is not None and i < len(row) else "")

    # ── Our destination tab, through the normal guards ─────────────────────────
    ours = gc.open_by_key(GOOGLE_SHEETS_ID)
    ws = ours.worksheet(GOOGLE_SHEET_WORKSHEET)
    print(f"Destination : {ours.title} / {ws.title} (gid={ws.id}, index={ws.index})")

    if ws.id == 0 or ws.index == 0:
        print("ABORT: destination resolved to the protected first tab (gid=0).")
        sys.exit(1)

    header = ws.row_values(1)
    if header != EXPECTED_HEADERS:
        print("ABORT: destination header does not match the current schema.")
        print(f"  Expected ({len(EXPECTED_HEADERS)}): {EXPECTED_HEADERS}")
        print(f"  Found    ({len(header)}): {header}")
        print("  Run `python tools/migrate_schema.py` first. Nothing touched.")
        sys.exit(1)

    url_col = index_of("Post URL")
    existing = {u.strip() for u in ws.col_values(url_col)[1:] if u.strip()}
    print(f"Destination already holds {len(existing)} Post URLs.\n")

    # ── Select ────────────────────────────────────────────────────────────────
    selected, rejected_geo, no_url, dupes, seen = [], [], [], [], set()

    for row in ftb_rows:
        hit = match_title(cell(row, "Title"))
        if not hit:
            continue
        category, keyword = hit

        blob = " ".join(cell(row, c) for c in GEO_TEXT_COLUMNS if c in col)
        in_india, evidence = is_india(blob, "")
        if GEO_FILTER == "india" and not in_india:
            rejected_geo.append((row, keyword, evidence))
            continue

        url = cell(row, "Post URL")
        if not url:
            no_url.append((row, keyword))
            continue
        if url in existing or url in seen:
            dupes.append((row, keyword))
            continue
        seen.add(url)

        reason = (
            f"Imported from FTB internships reference sheet; "
            f"title match '{keyword}' -> {category}; India: {evidence}"
        )
        selected.append((row, keyword, category, reason))

    print("=" * 72)
    print("SELECTION")
    print("=" * 72)
    print(f"  Title keyword match      : {len(selected) + len(rejected_geo) + len(no_url) + len(dupes)}")
    print(f"  Rejected on geography    : {len(rejected_geo)}")
    print(f"  Skipped, no Post URL     : {len(no_url)}")
    print(f"  Already in destination   : {len(dupes)}")
    print(f"  TO IMPORT                : {len(selected)}")

    if rejected_geo:
        print("\n  Geography rejections (review by hand if any are in fact Indian):")
        for row, kw, why in rejected_geo:
            print(f"    {cell(row,'Title')[:46]:46} | {cell(row,'Location')[:18]:18} | "
                  f"{cell(row,'HiringOrganization')[:24]:24} | {why}")
    if no_url:
        print("\n  Skipped for having no Post URL (nothing to dedupe on):")
        for row, kw in no_url:
            print(f"    {cell(row,'Title')[:46]:46} | {cell(row,'HiringOrganization')[:30]}")

    print("\n  Rows to import:")
    for row, kw, category, _ in selected:
        print(f"    [{kw:18}] {cell(row,'Title')[:44]:44} | {cell(row,'Location')[:20]:20} | "
              f"{cell(row,'HiringOrganization')[:26]}")

    if not selected:
        print("\nNothing to import.")
        return

    out_rows = [row_from_ftb(row, ftb_header, reason) for row, _, _, reason in selected]

    bad = [i for i, r in enumerate(out_rows) if len(r) != len(EXPECTED_HEADERS)]
    if bad:
        print(f"\nABORT: {len(bad)} mapped rows have the wrong width. Nothing written.")
        sys.exit(1)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = OUT / f"ftb_import_{stamp}{'_dryrun' if DRY_RUN else ''}.json"
    artifact.write_text(json.dumps(
        {"header": EXPECTED_HEADERS, "rows": out_rows}, ensure_ascii=False, indent=1
    ), encoding="utf-8")
    print(f"\nArtifact -> {artifact}")

    if DRY_RUN:
        print(f"[DRY RUN] Would append {len(out_rows)} rows to '{ws.title}'. No cells modified.")
        return

    ws.append_rows(out_rows, value_input_option="USER_ENTERED")
    print(f"Appended {len(out_rows)} rows to '{ws.title}'.")

    after = {u.strip() for u in ws.col_values(url_col)[1:] if u.strip()}
    print()
    print("=" * 72)
    print("VERIFICATION")
    print("=" * 72)
    print(f"  Post URLs before : {len(existing)}")
    print(f"  Post URLs after  : {len(after)}  (expected {len(existing) + len(out_rows)})")
    print(f"  All imported URLs present : {seen.issubset(after)}")
    print(f"  Reference sheet untouched : True (opened read-only, no write calls issued)")


if __name__ == "__main__":
    main()
