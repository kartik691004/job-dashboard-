# LinkedIn Hiring Intelligence — Search-Recall Improvement (STATE / HANDOFF)

> Scope guard: This work improves ONLY the Apify/LinkedIn SEARCH-QUERY generation
> layer to raise recall of genuine CURRENT Indian Founder's Office (FO) / Chief of
> Staff (CoS) vacancies. The verifier, DeterministicClassifier, India gate, hard
> gates, and acceptance logic are NEVER loosened. No Google Sheets writes. No
> production config changes until explicit approval.

## STATUS: BENCHMARK DONE — AWAITING APPROVAL to edit production SEARCH_QUERIES
Do NOT change `app/config.py` `SEARCH_QUERIES` until user approves the
recommended 12-query set below.

## Project location
- Root: `C:\Users\kartik\job dashboard`
- Pipeline: `app/orchestrator.py` -> `app/sources/datadoping_source.py` (Apify)
  -> `app/classifier.py` (DeterministicClassifier) -> `app/llm/verifier.py`
  (Groq) -> `app/sheets_writer.py`.
- Runtime: `.env` has `DRY_RUN=true`, `LLM_PROVIDER=groq`,
  `GROQ_MODEL=openai/gpt-oss-120b`, `GROQ_API_KEY`, `APIFY_API_TOKEN`,
  `SHEET_ID=15fuzMFlSj2zVaseYlMYdfRaFkmCeUoCyrvKxV6mxLis` (worksheet
  "LinkedIn Hiring Leads").

## Inspection findings (current ApifySource = app/sources/datadoping_source.py)
- Queries: hardcoded `SEARCH_QUERIES` (19) in `app/config.py`, sent as ONE
  `keywords` array to actor `datadoping/linkedin-posts-search-scraper`.
- Multi-query in one run: YES (keyword array).
- Dedup: post-run by `post_url` in orchestrator (`current_run_duplicates`).
  Benchmark observed 126 raw -> 80 unique URLs.
- `limitPerSource`: NOT used in live path (legacy `src/scrapers/apify_service.py`
  / `app/sources/apify_source.py` only; currently unused).
- `scrapeUntil`: NOT present in current pipeline (legacy concept, irrelevant).
- Date sorting/recency: ENFORCED — `run_input` sets `"sort_by": "date_posted"`
  and `"date_filter": "past-24h"`. MUST be preserved.

## New files created (none touch production pipeline)
- `tools/query_strategy.py`
  - `QUERY_MATRIX` (16 documented queries, Tiers A–E) with per-query docs:
    query, role, signal, india_anchor, why, expected_noise, india_relevance,
    role_coverage.
  - `build_query_matrix()`, `post_matches_query(text, query)` (tokenizer handles
    quoted phrases + unquoted anchors like `"chief of staff" India`),
    `ROLE_TERMS`/`HIRING_TERMS`/`INDIA_TERMS`.
- `tools/query_benchmark.py`
  - `analyze(posts, matrix)` -> per-query metrics (pure, offline; reuses
    `DeterministicClassifier` unchanged for the "deterministic_candidates" count).
  - `run_dry(sample_path)` (offline, no Apify/Sheets) and `run_live(limit)`
    (ONE small Apify run; writes only to `data/benchmark/`).
  - CLI: `--mode dry|live --sample <json> --limit 10 --out <json>`.
  - `print_report()` prints the per-query table.
- `tests/test_query_benchmark.py` — 7 offline tests (matrix form, matcher,
  analyze counts, empty corpus, flat-record mapping). All pass.

## Test suite
- Full offline: **234 passed, 1 skipped** (was 227 + 7 new).

## Live benchmark result (ONE run, max_posts=10, 16 candidate queries)
- Raw: 126 posts -> 80 unique URLs.
- **13 unique deterministic candidates, ALL India-relevant (10 FO, 3 CoS).**
- Per-query (matched_posts / deterministic_candidates / rate):
  - A1 `"founder's office" hiring`: 59 / 10 / 0.41  KEEP (FO backbone)
  - A2 `"founders office" hiring`: 7 / 0 / 0.0    DROP (no yield)
  - A3 `"founder's office associate" hiring`: 0 / 0  DROP (subset of A1)
  - A4 `"founder's office executive" hiring`: 0 / 0  DROP (subset of A1)
  - A5 `"chief of staff" hiring`: 32 / 5 / 0.31   KEEP (CoS backbone)
  - B1 `"founder's office" "we're hiring"`: 14 / 4 / 0.36  KEEP
  - B2 `"chief of staff" "we're hiring"`: 7 / 1 / 0.14  KEEP (optional)
  - B3 `"founder's office" "looking for"`: 21 / 5 / 0.29  KEEP
  - B4 `"chief of staff" "looking for"`: 20 / 3 / 0.20  KEEP
  - C1 `"founder's office" India`: 11 / 3 / 0.64  KEEP (high precision)
  - C2 `"chief of staff" India`: 13 / 4 / 0.62  KEEP (high precision)
  - D1 `"chief of staff" Bangalore`: 5 / 4 / 0.80  KEEP (best precision)
  - D2 `"founder's office" Mumbai`: 7 / 1 / 0.43  KEEP
  - D3 `"chief of staff" Delhi`: 4 / 1 / 0.75  KEEP
  - E1 `"chief of staff" apply`: 16 / 1 / 0.06  DROP (non-India noise)
  - E2 `"founder's office" apply`: 30 / 4 / 0.43  KEEP
- Note: `role_keyword_hits` equals `matched_posts` by design (matcher requires a
  role term). Raw scrape cached at `data/benchmark/benchmark_raw_*.json`;
  report at `data/benchmark/benchmark_*.json`.

## NOISE vs GENUINE
- Genuine (all 13 passed India-relevance): A1, A5, B1, B3, B4, C1, C2, D1–D3, E2.
- Pure noise/redundant: A2, A3, A4 (0 candidates; A3/A4 fully subsumed by A1);
  E1 (worst precision 0.06, mostly non-India).

## RECOMMENDED FINAL QUERY SET (12 queries; drops A2, A3, A4, E1)
Preserves ALL 13 observed candidates.
```
"founder's office" hiring
"chief of staff" hiring
"founder's office" "we're hiring"
"founder's office" "looking for"
"chief of staff" "looking for"
"founder's office" India
"chief of staff" India
"chief of staff" Bangalore
"chief of staff" Delhi
"founder's office" Mumbai
"founder's office" apply
"chief of staff" "we're hiring"   # optional
```

## Estimates & cost
- Recall: ~16 deterministic candidates per 100 UNIQUE posts (13/80); ~10/100 raw.
  Final ACCEPT = subset after the UNCHANGED LLM verifier (not run here to keep
  this a search-recall benchmark; verifier must stay strict).
- Cost: production 19 queries -> ~124–190 raw/run. Proposed 12 -> ~120 max.
  Apify ~$0.00155/post (~$0.19/run). Main win = less non-India noise + lower
  Groq verifier load (fewer junk posts sent to LLM).

## PENDING / NEXT STEPS (after approval)
1. On approval: replace `app/config.py` `SEARCH_QUERIES` (19) with the 12-query
   recommended set. Keep `sort_by=date_posted` + `date_filter=past-24h` intact.
2. Re-run the existing offline suite (expect 234 passed) + a DRY_RUN benchmark on
   the recovered sample to confirm.
3. Optionally run ONE more small live DRY_RUN end-to-end (orchestrator) to confirm
   candidates still reach the verifier; DO NOT weaken verifier.
4. Do NOT write to Google Sheets without explicit instruction.

## Hard constraints (do not violate)
- Never loosen DeterministicClassifier, India gate, LLM verifier, hard gates,
  DRY_RUN, Sheets writer, or acceptance logic.
- Do not run a large Apify scrape; use low limit; reuse existing API key.
- Stop and ask before changing production search config (currently awaiting).
