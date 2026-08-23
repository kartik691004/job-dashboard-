"""
measure_geo_candidates.py — evidence check before adding any term to app/geo.py.

Project rule (learned the hard way with "punjab" and ".in"): a geography term is never
added on intuition. Each candidate below is run against every corpus saved on disk and
its actual match contexts are printed, so a foreign collision is visible before the term
ships.

Read-only. Touches no network and no sheet.
"""
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

CANDIDATE_CITIES = [
    "ranchi", "patna", "dehradun", "guwahati", "nashik", "nasik", "rajkot", "ludhiana",
    "amritsar", "varanasi", "agra", "meerut", "faridabad", "ghaziabad", "navi mumbai",
    "mangaluru", "mangalore", "jamshedpur", "udaipur", "jodhpur", "gwalior",
    "aurangabad", "siliguri", "cuttack", "madurai", "tiruchirappalli", "raipur",
    "jalandhar", "dhanbad", "goa", "kota", "bharatpur", "thrissur", "kozhikode",
]
CANDIDATE_STATES = [
    "bihar", "jharkhand", "assam", "chhattisgarh", "uttarakhand", "himachal pradesh",
    "jammu", "tripura", "manipur", "meghalaya", "nagaland", "sikkim", "mizoram",
    "arunachal pradesh", "puducherry", "pondicherry",
]
CANDIDATE_MARKERS = [
    r"pvt\.?\s*ltd", r"private\s+limited", r"\bgst\b", r"aadhaar", r"\bupi\b",
    r"\bnit\b", r"\biiit\b", r"\bnift\b", r"\bxlri\b", r"\bnotice\s+period\b",
]


def load_corpora():
    """Every saved corpus, as (label, text) pairs. Missing files are skipped, not fatal."""
    out = []

    p = Path("data/analysis/2026-08-23_geo_projection.json")
    if p.exists():
        for rec in json.loads(p.read_text(encoding="utf-8")):
            blob = " ".join(str(v) for v in rec.values() if isinstance(v, str))
            out.append(("linkedin-corpus", blob))

    p = Path("data/analysis/ftb_reference_raw.json")
    if p.exists():
        snap = json.loads(p.read_text(encoding="utf-8"))
        for row in snap["rows"]:
            out.append(("ftb", " ".join(row)))

    for p in sorted(Path("data/runs").glob("*.json")) if Path("data/runs").exists() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        recs = data if isinstance(data, list) else data.get("posts", data.get("leads", []))
        for rec in recs if isinstance(recs, list) else []:
            if isinstance(rec, dict):
                out.append((p.name, " ".join(str(v) for v in rec.values() if isinstance(v, str))))

    return out


def report(title, candidates, corpora, is_regex=False):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)
    for cand in candidates:
        pattern = cand if is_regex else r"\b" + re.escape(cand).replace(r"\ ", r"\s+") + r"\b"
        rx = re.compile(pattern, re.I)
        hits, srcs = [], Counter()
        for label, blob in corpora:
            for m in rx.finditer(blob):
                s = max(0, m.start() - 45)
                hits.append(re.sub(r"\s+", " ", blob[s:m.end() + 45]))
                srcs[label] += 1
                break  # one context per record is enough to spot a collision
        verdict = "no corpus evidence" if not hits else f"{len(hits)} records {dict(srcs)}"
        print(f"\n  {cand!r:28} {verdict}")
        for h in hits[:4]:
            print(f"        ...{h}...")


corpora = load_corpora()
print(f"Loaded {len(corpora)} records from {len(set(l for l, _ in corpora))} corpora:")
for label, n in Counter(l for l, _ in corpora).most_common():
    print(f"    {n:5}  {label}")

report("CANDIDATE CITIES", CANDIDATE_CITIES, corpora)
report("CANDIDATE STATES", CANDIDATE_STATES, corpora)
report("CANDIDATE MARKERS", CANDIDATE_MARKERS, corpora, is_regex=True)
