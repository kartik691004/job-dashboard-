# Domain Docs

How the engineering skills should consume this repo's domain documentation.

## Before exploring, read these

- **`AI_CONTEXT.md`** at the repo root — this repo's single source of truth.
  It serves the role `CONTEXT.md` plays in other repos: product definition,
  architecture map, safety boundaries (Google Sheets / Apify budget), current
  status, and pending work. **Read it in full before any code change.**
- **`PENDING.md`** — session-by-session record of completed and outstanding work.
- **`README.md`** — operator-facing overview (API, scheduling, outreach, layout).

This repo deliberately does NOT have a separate `CONTEXT.md`: `AI_CONTEXT.md`
is canonical. Do not create a duplicate glossary file; extend `AI_CONTEXT.md`.

## Vocabulary (use these terms exactly)

- **lead** — a ClassifiedPost row written to Google Sheets (never "record"/"item").
- **deterministic classifier** — Stage-1 keyword/proximity/India gate
  (`app/classifier.py`), the cheap pre-filter.
- **LLM gate** — Stage-2 Groq semantic verifier (`app/llm/verifier.py`);
  decisions: ACCEPT / REVIEW / REJECT. REVIEW leads are NOT confirmed leads.
- **candidate** — a deterministic-passing post sent to the LLM gate.
- **worksheet** — exactly `LinkedIn Hiring Leads`; the first tab (gid=0) is the
  protected Job Board and is untouchable.
- **dry run** — `DRY_RUN=true`: everything runs except ANY Sheets write.

## Architecture decisions worth knowing

- Deterministic classifier stays; the LLM is an additive bridge (2026-08-25).
- Hard gates live in Python (`Verifier._gate`), never in prompt trust.
- Only deterministic candidates are sent to Groq (cost boundary).
