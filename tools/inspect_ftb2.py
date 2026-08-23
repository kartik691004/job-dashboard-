"""
inspect_ftb2.py — follow-up read-only questions on the FTB snapshot saved locally.

Reads data/analysis/ftb_reference_raw.json (no network, no writes). Two things the
column mapping needs:

  1. FTB's controlled vocabulary for its small enum-ish columns (Type, Timing, Tag).
     Our rows should speak FTB's words, not invent parallel ones.
  2. The 4 rows that matched a title keyword but failed the India filter, in full, so
     the include/exclude call is made on evidence rather than on "it's an India board".
"""
import io
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

from app.config import ROLE_CATEGORIES
from app.geo import is_india

snap = json.loads(Path("data/analysis/ftb_reference_raw.json").read_text(encoding="utf-8"))
header, rows = snap["header"], snap["rows"]
idx = {n: i for i, n in enumerate(header) if n.strip()}


def cell(row, name):
    i = idx.get(name)
    return (row[i].strip() if i is not None and i < len(row) else "")


print("=" * 72)
print("CONTROLLED VOCABULARY")
print("=" * 72)
for col in ("Type", "Timing", "Experience", "Tag", "Duration"):
    c = Counter(cell(r, col) for r in rows)
    print(f"\n{col} ({len(c)} distinct):")
    for v, n in c.most_common(12):
        print(f"    {n:4}  {v!r}")

print("\n" + "=" * 72)
print("DATE / URL FORMATS")
print("=" * 72)
for col in ("Date Added", "Deadline"):
    vals = [cell(r, col) for r in rows if cell(r, col)]
    print(f"{col:12} {len(vals)} filled, samples: {vals[:4]}")
for col in ("Post URL", "Hiring Manager linkedin", "Form link", "Contact Email", "Stipend"):
    vals = [cell(r, col) for r in rows if cell(r, col)]
    print(f"{col:24} {len(vals):4}/{len(rows)} filled, sample: {vals[0][:80] if vals else '-'!r}")

print("\n" + "=" * 72)
print("TITLE-MATCHED ROWS THAT FAILED THE INDIA FILTER")
print("=" * 72)
for r in rows:
    title = cell(r, "Title").lower()
    hit = None
    for cat, kws in ROLE_CATEGORIES.items():
        for kw in kws:
            if kw in title:
                hit = (cat, kw)
                break
        if hit:
            break
    if not hit:
        continue
    blob = " ".join(cell(r, c) for c in ("Title", "Description", "Location", "Stipend") if c in idx)
    ok, why = is_india(blob, "")
    if ok:
        continue
    print("-" * 72)
    print(f"  matched   : {hit[1]!r} -> {hit[0]}")
    print(f"  geo verdict: {why}")
    for name in ("Title", "Location", "HiringOrganization", "Stipend", "Tag"):
        print(f"  {name:20} = {cell(r, name)[:90]}")
    print(f"  Description         = {cell(r, 'Description')[:300]}")

print("\n" + "=" * 72)
print("THE TWO 'generalist' TITLE MATCHES")
print("=" * 72)
for r in rows:
    if "generalist" in cell(r, "Title").lower():
        print("-" * 72)
        for name in ("Title", "Location", "HiringOrganization", "Tag", "Type"):
            print(f"  {name:20} = {cell(r, name)[:90]}")
        print(f"  Description         = {cell(r, 'Description')[:250]}")
