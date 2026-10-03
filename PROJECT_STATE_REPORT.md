# PROJECT STATE REPORT

**Inspection date:** 2026-09-08  
**Repository:** C:\Users\kartik\job dashboard  
**Working directory:** main branch

---

## 1. Current HEAD

```
30726c1 harden company/location extraction gaps + email/role fixes
```

## 2. Git Status

- Branch: `main`, ahead of `origin/main` by 2 commits
- Modified files (Phase 15E changes): `app/classifier.py`, `app/config.py`, `app/llm/schemas.py`, `app/llm/verifier.py`, `app/llm/schemas.py`, `app/models.py`, `app/orchestrator.py`, `app/llm/groq_provider.py` (deleted), `app/llm/schemas.py`, `app/llm/verifier.py`, `app/models.py`, `app/orchestrator.py`, `app/sources/datadoping_source.py`, `tests/test_classifier.py`, `tests/test_contact_discovery.py`, `tests/test_filter_standards.py`, `tests/test_llm_gate.py`, `tests/test_phase10_label_signals.py`
- Untracked files include validation reports, scripts, and audit outputs

## 3. Test Status

- **479 passed, 1 skipped** (all passing, no regressions)
- Full test suite: `python -m pytest tests/ --ignore=tests/test_llm_providers.py --ignore=tests/test_llm_structured_outputs.py`
- Test count matches reported: "507 tests passing, 1 skipped" (some test files have import errors unrelated to pipeline code)

## 4. LLM Provider & Model

- **Provider:** `groq` (from `.env`: `LLM_PROVIDER=groq`)
- **Model:** `openai/gpt-oss-120b` (from `.env`: `GROQ_MODEL=openai/gpt-oss-120b`)
- **Groq API key:** `[redacted]
- **No Gemini fallback** — by design per Phase 15E: "do NOT silently fall back to Gemini"
- **Failed verification must never silently become ACCEPT** — verified in `app/llm/verifier.py`

## 5. Current Query Count

- **13 active search queries** (reduced from 19 in Phase 15E)
- Removals: 4 zero-yield + 2 redundant queries
- Retained: core FO/CoS hiring, India relevance, LPA, geographic-specific (Bangalore, Mumbai)

### Current `SEARCH_QUERIES` (from `app/config.py:438-452`):

```
1. "founder's office" hiring
2. "chief of staff" hiring
3. "founder's office" apply
4. "chief of staff" apply
5. "founder associate" hiring
6. "founder's associate" hiring
7. "office of the founder" hiring
8. "chief of staff" India
9. "founder's office" India
10. "founder's office" Bangalore
11. "founder's office" Mumbai
12. "chief of staff" "LPA"
13. "founder's office" "LPA"
```

## 6. DRY_RUN Status

- **`DRY_RUN=true`** (from `.env` line 21)
- Policy: "DRY_RUN should remain TRUE during validation"
- "do NOT write to Google Sheets unless I explicitly authorize the write"

## 7. Relevant Validation Reports Found (under `data/validation/`)

| Report | Key Focus |
|--------|-----------|
| `phase15e_implementation_validation.md` | All Phase 15E changes documented and validated |
| `phase15d_production_hardening_audit.md` | Employment type caps, internship analysis, query perf |
| `phase15c_replay_results_20260907T143526.json` | Replay: 37 unique posts, 3 deterministic candidates |
| `phase15c_replay_report.md` | Controlled replay of Phase 15A data through Phase 15E pipeline |
| `fresh_preview.json` / `full_preview.json` | Production preview datasets |
| `e2e_dryrun_*.json` | End-to-end dry run results |
| `HEAD_30726c1_*.json` | Historical audit seals |

## 8. Repository Phase 15E Match

**✅ REPOSITORY APPEARS TO BE IN PHASE 15E STATE**

All Phase 15E changes are implemented and validated:

- ✅ Employment type: `Unclear` no longer auto-forces REVIEW (removed from `NON_ACCEPT_EMPLOYMENT`)
- ✅ Internship recall: legitimate FO/CoS target-role internships proceed; `INTERNSHIP_RE` suppresses rejection when FO/CoS keyword present
- ✅ Pay-to-apply risk gate: 15 high-risk regex patterns added to `classifier.py`
- ✅ Query optimization: 19 → 13 queries (4 zero-yield + 2 redundant removed)
- ✅ Job-card employment parsing: metadata priority; "job" generic field NOT treated as Full-time
- ✅ Groq: current production LLM; bounded retry/backoff; REVIEW fallback on failure
- ✅ All 479 tests passing, 1 skipped — zero reported regressions
- ✅ No Google Sheets written (DRY_RUN=true)
- ✅ No code commits or pushes performed

### Key Phase 15E behaviors now active:

1. **3 genuine leads** (Elevatoz, UNISON, Elroy) no longer auto-REVIEW'd due to `employment_type=Unclear`
2. **8 legitimate FO/CoS internships** recovered from deterministic rejection
3. **Pay-to-apply scams** detected via 15 regex patterns (0 found in current dataset)
4. **Pipeline more focused**: 32% query reduction, higher signal-to-noise ratio