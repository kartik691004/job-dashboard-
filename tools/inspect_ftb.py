"""
inspect_ftb.py — READ-ONLY inspection of the FTB reference sheet + backup of ours.

The FTB spreadsheet (1f6b...) is reference material only: this script opens it, reads,
and never writes. It also backs up our own LinkedIn Hiring Leads tab to disk, because
the next step re-headers that tab and a layout change is not reversible from the API.

Answers three questions the import depends on:
  1. What is FTB's exact header + a full sample row (to design the column mapping)?
  2. For each FTB row, WHICH role keyword matched and in WHICH column? The earlier
     count of 116 "Chief of Staff" matches included "HR Intern" and "Recruitment
     Intern", so matching is clearly firing somewhere too loose to trust.
  3. Which rows pass the India geo filter, using FTB's own Location column?
"""
import io
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import gspread

from app.config import GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID, GOOGLE_SHEET_WORKSHEET, ROLE_CATEGORIES
from app.geo import is_india

FTB_ID = "1f6b_QgkmeOAu0XFpiYyDHMgfkXFKEsrX4gl8IjWbI1k"
OUT = Path("data/analysis")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    gc = gspread.service_account(filename=GOOGLE_SHEETS_CREDENTIALS)

    # ── 1. FTB reference (read-only) ──────────────────────────────────────────
    ftb = gc.open_by_key(FTB_ID)
    ws = ftb.get_worksheet(0)
    print(f"FTB spreadsheet : {ftb.title}")
    print(f"FTB tab         : {ws.title} (gid={ws.id})")

    values = ws.get_all_values()
    header = values[0]
    rows = [r for r in values[1:] if any(c.strip() for c in r)]
    print(f"FTB header ({len(header)} cols): {header}")
    print(f"FTB data rows   : {len(rows)}\n")

    (OUT / "ftb_reference_raw.json").write_text(
        json.dumps({"header": header, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"Saved FTB snapshot -> {OUT / 'ftb_reference_raw.json'}\n")

    idx = {name: i for i, name in enumerate(header) if name.strip()}
    print("Column index map:")
    for k, v in idx.items():
        print(f"  [{v:2}] {k}")

    def cell(row, name):
        i = idx.get(name)
        return (row[i].strip() if i is not None and i < len(row) else "")

    print("\n" + "=" * 72)
    print("FULL SAMPLE ROWS (to design the mapping)")
    print("=" * 72)
    for r in rows[:3]:
        print("-" * 72)
        for name, i in idx.items():
            val = r[i] if i < len(r) else ""
            if val.strip():
                print(f"  {name:24} = {val[:160]}")

    # ── 2. Where does each keyword actually match? ────────────────────────────
    print("\n" + "=" * 72)
    print("KEYWORD MATCH PROVENANCE")
    print("=" * 72)

    title_hits, desc_only_hits = [], []
    kw_counter_title, kw_counter_desc = Counter(), Counter()

    for r in rows:
        title = cell(r, "Title").lower()
        blob = " ".join(
            cell(r, c).lower() for c in ("Description", "Tags", "Similar Fields", "Tag", "Similar Field Tags")
            if c in idx
        )
        t_match = None
        for cat, kws in ROLE_CATEGORIES.items():
            for kw in kws:
                if kw in title:
                    t_match = (cat, kw)
                    break
            if t_match:
                break
        if t_match:
            title_hits.append((r, t_match))
            kw_counter_title[t_match[1]] += 1
            continue
        d_match = None
        for cat, kws in ROLE_CATEGORIES.items():
            for kw in kws:
                if kw in blob:
                    d_match = (cat, kw)
                    break
            if d_match:
                break
        if d_match:
            desc_only_hits.append((r, d_match))
            kw_counter_desc[d_match[1]] += 1

    print(f"\nMatched in TITLE           : {len(title_hits)}")
    for kw, n in kw_counter_title.most_common():
        print(f"    {n:4}  {kw!r}")
    print(f"\nMatched ONLY in body/tags  : {len(desc_only_hits)}")
    for kw, n in kw_counter_desc.most_common():
        print(f"    {n:4}  {kw!r}")

    print("\nSample TITLE matches:")
    for r, (cat, kw) in title_hits[:20]:
        print(f"    [{kw:22}] {cell(r,'Title')[:70]:70} | {cell(r,'Location')[:28]}")

    print("\nSample BODY-ONLY matches (these are the suspicious ones):")
    for r, (cat, kw) in desc_only_hits[:20]:
        print(f"    [{kw:22}] {cell(r,'Title')[:70]:70} | {cell(r,'Location')[:28]}")

    # ── 3. Geo on FTB's own Location column ──────────────────────────────────
    print("\n" + "=" * 72)
    print("GEO ON TITLE-MATCHED ROWS")
    print("=" * 72)
    passed, failed = 0, Counter()
    for r, _ in title_hits:
        loc = cell(r, "Location")
        blob = " ".join(cell(r, c) for c in ("Title", "Description", "Location", "Stipend") if c in idx)
        ok, why = is_india(blob, "")  # location column is free text, not a LinkedIn job card
        if ok:
            passed += 1
        else:
            failed[loc or "(blank)"] += 1
    print(f"Title-matched rows passing India filter: {passed} / {len(title_hits)}")
    print("Top rejected Location values:")
    for loc, n in failed.most_common(15):
        print(f"    {n:4}  {loc}")

    # ── 4. Back up OUR tab before any re-header ──────────────────────────────
    print("\n" + "=" * 72)
    print("BACKUP OF OUR TAB")
    print("=" * 72)
    ours = gc.open_by_key(GOOGLE_SHEETS_ID)
    our_ws = ours.worksheet(GOOGLE_SHEET_WORKSHEET)
    our_vals = our_ws.get_all_values()
    bak = OUT / "jobdashboard_linkedin_leads_backup_pre_migration.json"
    bak.write_text(
        json.dumps({
            "spreadsheet": ours.title, "spreadsheet_id": GOOGLE_SHEETS_ID,
            "worksheet": our_ws.title, "gid": our_ws.id,
            "rows": our_vals,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{ours.title} / {our_ws.title}: {len(our_vals)} rows (incl. header)")
    print(f"Backed up -> {bak}")


if __name__ == "__main__":
    main()
