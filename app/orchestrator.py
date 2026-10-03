from typing import Dict, Any, List, Tuple
import threading

from app.sources.datadoping_source import DataDopingSource
from app.classifier import DeterministicClassifier
from app.sheets_writer import SheetsWriter, SheetReadError, WriteResult
from app.config import (
    APIFY_API_TOKEN,
    GOOGLE_SHEETS_CREDENTIALS,
    GOOGLE_SHEETS_ID,
    GOOGLE_SHEET_WORKSHEET,
    GOOGLE_SHEET_DATA_WORKSHEET,
    DRY_RUN,
    PRODUCTION_GATE,
    ALLOW_UNGATED_PRODUCTION_WRITE,
    SEARCH_QUERIES,
    CONTACT_PROVIDER,
    CONTACT_CACHE_PATH,
    CONTACT_MAX_PAGES,
    CONTACT_MAX_REQUESTS,
    CONTACT_MIN_INTERVAL_S,
    CONTACT_TIMEOUT_S,
)
from app.llm.verifier import Verifier, enriched_post
from app.enrichment import Enricher
from app.contact import get_contact_provider

# USD per returned post on the datadoping actor (FREE plan, $5 per cycle from the 11th).
PER_POST_USD = 0.00155

# Phase 22C: single-process serialization for the Sheet read+write section.
# Only one pipeline run in THIS process may hold it at a time; a second run
# that arrives while it is held skips the write phase with
# write_outcome="RUN_ALREADY_ACTIVE" instead of racing col_values+append_rows.
# LIMITATION (documented, not solved here): this is a process-local lock. Two
# independent processes/hosts can still race; that requires an external
# distributed lock or stronger Sheet-side idempotency (Phase 23 candidate).
_PRODUCTION_WRITE_LOCK = threading.Lock()

class Orchestrator:
    def __init__(self, production_gate: bool | None = None):
        # Phase 24: direct-company + verified-HM production gate. Default OFF
        # (None → PRODUCTION_GATE env, default false) preserves current pipeline
        # behavior byte-for-byte; opt in per-run for production mode. When ON,
        # ACCEPT-path leads must also pass app.production_gate or they are held
        # as Review / rejected and never reach the Sheet as New.
        self.production_gate_enabled = (
            PRODUCTION_GATE if production_gate is None else bool(production_gate))
        # datadoping/linkedin-posts-search-scraper replaced supreme_coder/linkedin-post,
        # whose content search has returned "No posts found" for every query since
        # 2026-08-23. This actor takes keywords directly and covers all of them in ONE
        # run, so a pipeline execution costs one run instead of one-per-query.
        self.source = DataDopingSource(APIFY_API_TOKEN)
        self.classifier = DeterministicClassifier()
        self.sheets_writer = SheetsWriter(GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID, GOOGLE_SHEET_WORKSHEET, DRY_RUN,
                                          data_worksheet_name=GOOGLE_SHEET_DATA_WORKSHEET)
        # Stage 2 semantic gate. Disabled mode (no LLM key) holds every
        # candidate at Status=Review instead of auto-accepting anything.
        self.verifier = Verifier()
        # Phase 6: LEAD ENRICHMENT — runs only on leads that pass ACCEPT.
        # Deterministic-only by default (no extra LLM cost). It never changes
        # the vacancy decision; it only makes an accepted lead more complete.
        # Phase 8: when CONTACT_PROVIDER=web_search is explicitly set (approved
        # validation only), ACCEPTED leads additionally get live verified-contact
        # discovery. The default remains offline/fail-closed (null).
        _contact_provider = None
        if CONTACT_PROVIDER and CONTACT_PROVIDER != "null":
            _contact_provider = get_contact_provider(
                CONTACT_PROVIDER,
                cache_path=CONTACT_CACHE_PATH,
                max_pages=CONTACT_MAX_PAGES,
                max_requests=CONTACT_MAX_REQUESTS,
                min_interval_s=CONTACT_MIN_INTERVAL_S,
                timeout_s=CONTACT_TIMEOUT_S,
            )
        self.enricher = Enricher(contact_provider=_contact_provider)
        # Per-lead detail from the most recent run (dry-run reports, dashboards).
        self._accepted_details: List[Dict[str, Any]] = []
        self._review_details: List[Dict[str, Any]] = []
        self._rejected_samples: List[Dict[str, Any]] = []
        # Enriched copies of ACCEPTED leads, keyed by Source Link.
        self._enrichment_results: Dict[str, Any] = {}

    def run_pipeline(self, limit: int = 10) -> Dict[str, Any]:
        """
        Execute the full LinkedIn Hiring Intelligence pipeline.

        :param limit: Maximum posts requested per keyword. The actor floors this at 10.
                      Must be a positive integer (validated at the API layer).
        """
        errors = 0
        print("Starting pipeline...")
        self._accepted_details = []
        self._review_details = []
        self._rejected_samples = []
        self._enrichment_results = {}

        # Phase 25.1 production-invocation guard (fail fast, before Apify
        # spend): a REAL (non-dry) Sheet write through the legacy gate-off
        # path is refused. Production writes require the Phase-24 gate
        # (production_gate=True) or explicit ALLOW_UNGATED_PRODUCTION_WRITE=1.
        # DRY_RUN executions and unit-test mocks are never affected.
        _writer_live = not getattr(self.sheets_writer, "dry_run", True)
        if _writer_live and not self.production_gate_enabled \
                and not ALLOW_UNGATED_PRODUCTION_WRITE:
            _guard_error = ("REFUSED: live Sheet write with the Phase-24 "
                            "production gate disabled. Re-run with "
                            "production_gate=True (or PRODUCTION_GATE=true) "
                            "for a filtered production write, keep DRY_RUN "
                            "for validation, or set "
                            "ALLOW_UNGATED_PRODUCTION_WRITE=1 for an explicit "
                            "legacy maintenance write.")
            print(_guard_error)
            return {"scraped": 0, "unique": 0, "valid": 0, "excluded": 0,
                    "current_run_duplicates": 0, "sheet_duplicates": 0,
                    "new_rows": 0, "written_rows": 0, "write_outcome": "GATE_REQUIRED",
                    "write_error": _guard_error, "sheet_read_ok": False,
                    "run_already_active": False, "errors": 1,
                    "deterministic_candidates": 0, "llm_calls": 0,
                    "accepted": 0, "review": 0, "llm_rejected": 0,
                    "production_held": 0, "production_rejected": 0}

        # 1. Scrape — all SEARCH_QUERIES keywords in a single actor run
        est_posts = len(SEARCH_QUERIES) * max(limit, 10)
        print(f"Scraping {len(SEARCH_QUERIES)} keywords in one actor run "
              f"(max {max(limit, 10)}/keyword, ceiling {est_posts} posts, ~${est_posts * PER_POST_USD:.2f})")
        try:
            raw_posts = self.source.search_all(SEARCH_QUERIES, max_posts=limit)
        except Exception as e:
            print(f"Scrape error: {e}")
            return {"scraped": 0, "unique": 0, "valid": 0, "excluded": 0,
                    "current_run_duplicates": 0, "sheet_duplicates": 0,
                    "new_rows": 0, "errors": 1,
                    "deterministic_candidates": 0, "llm_calls": self.verifier.llm_calls,
                    "accepted": 0, "review": 0, "llm_rejected": 0}
        if self.source.last_errors:
            errors += len(self.source.last_errors)
            print(f"Actor reported {len(self.source.last_errors)} unusable records; "
                  f"first: {self.source.last_errors[0]}")
        print(f"Scraped {len(raw_posts)} posts.")

        # 3. Level 1 Deduplication — current run
        unique_raw_posts = []
        seen_urls: set = set()
        for p in raw_posts:
            if p.post_url not in seen_urls:
                seen_urls.add(p.post_url)
                unique_raw_posts.append(p)

        current_run_duplicates = len(raw_posts) - len(unique_raw_posts)

        # Phase 25.5: deterministic LLM-budget prioritization (internal-only
        # sourcing score). Higher-signal posts verify FIRST so quota
        # exhaustion degrades the weakest candidates, never the strongest.
        # Stable sort: ties keep scrape order, identical posts score
        # identically (existing order-dependent tests unaffected). The SET
        # verified is unchanged — order only, no discards, no gate change.
        try:
            from app.sourcing_score import score_raw_post
            unique_raw_posts.sort(key=lambda p: -score_raw_post(p))
        except Exception:
            pass  # scoring must never break a run; keep scrape order

        # 4. Classify (deterministic pre-filter) + LLM quality gate.
        # Only deterministic-passing candidates are sent to the LLM — that is
        # the cost-control boundary (brief §25).
        accepted_posts: List = []
        review_posts: List = []
        # Phase 24.2: (merged, gate) pairs that passed the production gate
        # AND routed to a production tab. Only these may reach the writer
        # when the gate is enabled; the writer re-validates them fail-closed.
        production_pairs: List[Tuple] = []
        excluded_count = 0
        deterministic_candidates = 0
        accepted = 0
        review = 0
        llm_rejected = 0
        production_held = 0
        production_rejected = 0

        for p in unique_raw_posts:
            try:
                classified = self.classifier.classify(p)
            except Exception as e:
                import traceback
                print(f"Classifier error on {p.post_url}: {e}\n{traceback.format_exc()}")
                errors += 1
                excluded_count += 1
                continue
            if not classified.is_valid:
                excluded_count += 1
                continue
            deterministic_candidates += 1
            try:
                gate = self.verifier.verify(
                    post_text=classified.text,
                    post_url=classified.post_url,
                    author_name=classified.author_name,
                    author_profile_url=classified.author_profile_url,
                    job_card_location=classified.job_card_location,
                    job_card_company=classified.job_card_company,
                    job_card_employment_type=classified.job_card_employment_type,
                    job_card_experience=classified.job_card_experience,
                )
            except Exception as e:
                # The verifier is built to never raise; this is belt-and-braces.
                print(f"Verifier crashed on {p.post_url}: {e}")
                errors += 1
                llm_rejected += 1
                continue
            if gate.decision == "REJECT":
                llm_rejected += 1
                # gate.reasons is already complete: for an LLM rejection it is
                # ["LLM rejected", <llm rationale>]; for a hard-gate rejection it
                # is the specific hard reason(s). Appending verdict.reason again
                # would duplicate text or contradict the hard gate, so we don't.
                self._rejected_samples.append({
                    "source_link": p.post_url,
                    "reasons": gate.reasons,
                    "snippet": classified.text[:120],
                })
                continue
            merged = enriched_post(classified, gate)
            row = {
                "company": merged.company_name,
                "major_category": merged.major_category,
                "exact_role": merged.exact_role,
                "ctc": merged.ctc,
                "location": merged.location,
                "experience": merged.experience_requirement,
                "employment_type": merged.employment_type,
                "india_relevance": merged.india_relevance,
                "confidence": merged.confidence,
                "reason": merged.classification_reason[:200],
                "status": merged.status,
                "cold_email": merged.cold_email,
                "source_url": merged.source_link,
                "hiring_manager_name": merged.hiring_manager_name,
                "hiring_manager_linkedin": merged.hiring_manager_linkedin,
                "hiring_manager_evidence": merged.hiring_manager_evidence,
                "apply_google_form": merged.apply_google_form,
                "apply_link": merged.apply_link,
                "key_points": merged.key_points,
                "post_date": merged.post_date,
                "description": merged.description,
            }
            if gate.decision == "ACCEPT":
                # Phase 24.2 production boundary (only when enabled; default
                # OFF keeps the pre-24 path byte-identical). The production
                # gate runs BEFORE routing: only ELIGIBLE records route to a
                # production tab and reach the writer. Anything else STOPS
                # here — HOLD joins Review (internal artifact only, never a
                # production tab), REJECT joins the rejected samples.
                if self.production_gate_enabled:
                    from app.production_gate import (
                        DATA_PRODUCTION,
                        MAIN_PRODUCTION,
                        route_production_post,
                    )
                    _route = route_production_post(merged, gate)
                    if _route.destination not in (MAIN_PRODUCTION,
                                                  DATA_PRODUCTION):
                        if _route.destination == "REJECT":
                            production_rejected += 1
                            llm_rejected += 1
                            self._rejected_samples.append({
                                "source_link": p.post_url,
                                "reasons": list(gate.reasons) + [
                                    "Production gate REJECT: %s" % r
                                    for r in _route.reasons],
                                "snippet": classified.text[:120],
                            })
                        else:
                            production_held += 1
                            review += 1
                            merged.status = "Review"
                            review_posts.append(merged)
                            _hold_row = dict(row)
                            _hold_row["status"] = "Review"
                            _hold_row["reason"] = (
                                row["reason"] + " | Production gate HOLD: "
                                + "; ".join(_route.reasons))[:200]
                            self._review_details.append(_hold_row)
                        continue
                    production_pairs.append((merged, gate))
                accepted += 1
                accepted_posts.append(merged)
                # Phase 6: enrich the ACCEPTED lead (adds completeness/evidence
                # without changing the vacancy decision). Stored keyed by source.
                try:
                    enriched = self.enricher.enrich_from_classified(
                        merged, 
                        gate.verdict,
                        job_card_company=classified.job_card_company,
                        job_card_employment_type=classified.job_card_employment_type,
                        job_card_experience=classified.job_card_experience,
                    )
                except Exception as e:  # belt-and-braces; must never break runs
                    print(f"Enrichment error on {merged.source_link}: {e}")
                    enriched = None
                if enriched is not None:
                    self._enrichment_results[merged.source_link] = enriched
                    row["enrichment"] = _enrichment_row(enriched)
                self._accepted_details.append(row)
            else:
                review += 1
                review_posts.append(merged)
                self._review_details.append(row)

        # Phase 24.2: with the gate enabled, ONLY production-eligible pairs
        # are sheet candidates. Held Review rows stay in _review_details
        # (internal artifact) and never reach either production tab.
        if self.production_gate_enabled:
            valid_posts = [p for (p, _g) in production_pairs]
        else:
            valid_posts = accepted_posts + review_posts

        # 5. Level 2 Deduplication — Google Sheet (Phase 22A: FAIL CLOSED).
        # A successful read returns the URL set — possibly EMPTY, which is a
        # valid Sheet state. A failed read raises SheetReadError and ABORTS
        # the write phase: "could not read existing URLs" is never treated
        # as "there are no existing URLs".
        sheet_read_ok = True
        sheet_read_error = ""
        existing_urls: set = set()
        new_valid_posts = []
        # Phase 24.2: gate-enabled write candidates as (merged, gate) pairs.
        new_production_pairs: List[Tuple] = []
        sheet_duplicates = 0
        # 6. Write state (Phase 22B: truthful — written_rows counts ONLY
        # confirmed rows; new_rows mirrors it on terminal failure).
        write_outcome = "SKIPPED"
        write_error = ""
        written_rows = 0
        main_written = 0  # Phase 24.1: confirmed rows per production tab
        data_written = 0
        routing_dropped = 0  # Phase 25.2: pre-write tab/category assertion drops
        run_already_active = False
        legacy_writer = False  # True when write_posts returned no WriteResult

        # Phase 22C: serialize the read+write section within this process so
        # two overlapping runs cannot both see a URL as absent and append it.
        write_lock_held = _PRODUCTION_WRITE_LOCK.acquire(blocking=False)
        if not write_lock_held:
            run_already_active = True
            write_outcome = "RUN_ALREADY_ACTIVE"
            write_error = ("Another pipeline run holds the production write "
                           "lock; skipping the Sheet read+write section.")
            print(f"Sheet write skipped: {write_error}")
        else:
            try:
                try:
                    existing_urls = self.sheets_writer.get_existing_urls()
                except SheetReadError as e:
                    sheet_read_ok = False
                    sheet_read_error = str(e)
                except Exception as e:  # fail closed on any unexpected raiser
                    sheet_read_ok = False
                    sheet_read_error = f"Sheet URL read failed: {e}"
                if not sheet_read_ok:
                    errors += 1
                    write_outcome = "READ_FAILED"
                    write_error = sheet_read_error
                    print(f"Sheet write ABORTED: {sheet_read_error}")
                else:
                    if self.production_gate_enabled:
                        # Production boundary: dedup + write ELIGIBLE pairs
                        # only, via the hardened writer (which re-validates
                        # every pair fail-closed). Held/Review rows never
                        # enter this branch, so they can never reach a tab.
                        for pair in production_pairs:
                            if pair[0].source_link in existing_urls:
                                sheet_duplicates += 1
                            else:
                                new_production_pairs.append(pair)
                    else:
                        for p in valid_posts:
                            if p.source_link in existing_urls:
                                sheet_duplicates += 1
                            else:
                                new_valid_posts.append(p)

                    # 6. Write to Sheets (skipped when DRY_RUN=True). Gate OFF:
                    # ACCEPT rows carry Status=New, REVIEW rows Status=Review —
                    # never mixed, never auto-trusted (legacy behavior,
                    # frozen). Gate ON: only production-eligible pairs are
                    # written, each to exactly one production tab.
                    pending = (new_production_pairs if self.production_gate_enabled
                               else new_valid_posts)
                    if pending:
                        try:
                            if self.production_gate_enabled:
                                result = self.sheets_writer.write_production(
                                    new_production_pairs)
                            else:
                                result = self.sheets_writer.write_posts(new_valid_posts)
                        except Exception as e:
                            # Escaped write_posts itself (row-build or worse):
                            # the real writer converts append outcomes into a
                            # WriteResult, so no append result exists here.
                            errors += 1
                            write_outcome = "FAILED"
                            write_error = f"Writer raised before result: {e}"
                            print(f"Sheet write error: {e}")
                            result = None
                        except Exception as e:
                            # Escaped write_posts itself (row-build or worse):
                            # the real writer converts append outcomes into a
                            # WriteResult, so no append result exists here.
                            errors += 1
                            write_outcome = "FAILED"
                            write_error = f"Writer raised before result: {e}"
                            print(f"Sheet write error: {e}")
                            result = None
                        if result is not None:
                            if isinstance(result, WriteResult):
                                write_outcome = result.outcome
                                write_error = result.error
                                if result.outcome == "SUCCESS":
                                    written_rows = result.written
                                elif result.outcome in ("DRY_RUN", "EMPTY",
                                                           "DUPLICATE"):
                                    # Phase 32.2: idempotent no-op — nothing
                                    # new appended, no error. (Unreachable via
                                    # the orchestrator pre-filter; defensive
                                    # for direct-writer parity.)
                                    written_rows = 0
                                else:  # FAILED / UNKNOWN from the real writer
                                    errors += 1
                                    written_rows = 0
                                # Phase 24.1: confirmed rows per production tab.
                                dests = getattr(result, "destinations", None) or {}
                                main_written = int(dests.get(
                                    GOOGLE_SHEET_WORKSHEET, 0))
                                data_written = int(dests.get(
                                    GOOGLE_SHEET_DATA_WORKSHEET, 0))
                                # Phase 25.2: records dropped by the pre-write
                                # tab/category assertion (fail-closed policy
                                # drops, never append failures).
                                routing_dropped = int(getattr(
                                    result, "routing_dropped", 0) or 0)
                            else:
                                # Legacy/mock writer without a structured result
                                # (production always returns WriteResult): keep
                                # historical counting, but never claim success —
                                # outcome stays UNKNOWN, written stays 0.
                                write_outcome = "UNKNOWN"
                                write_error = ("Writer returned no structured "
                                               "WriteResult; outcome unknown.")
                                written_rows = 0
                                legacy_writer = True
                    else:
                        write_outcome = "EMPTY"
            finally:
                _PRODUCTION_WRITE_LOCK.release()

        # 7. Summary
        # written_rows counts ONLY explicitly confirmed appends. new_rows keeps
        # its historical meaning (candidate rows that reached the write phase)
        # except on terminal failure, where it is 0 — a failed write is never
        # reported as rows written. (The legacy_writer branch preserves
        # historical counting for non-structured writers such as test mocks;
        # production always yields a WriteResult.)
        if write_outcome in ("FAILED", "READ_FAILED", "RUN_ALREADY_ACTIVE"):
            new_rows = 0
        elif write_outcome == "UNKNOWN" and not legacy_writer:
            new_rows = 0  # ambiguous real-writer outcome: claim nothing
        else:
            new_rows = len(new_valid_posts)
        summary = {
            "scraped": len(raw_posts),
            "unique": len(unique_raw_posts),
            "valid": accepted,               # confirmed leads only
            "excluded": excluded_count,
            "current_run_duplicates": current_run_duplicates,
            "sheet_duplicates": sheet_duplicates,
            "new_rows": new_rows,
            "written_rows": written_rows,    # Phase 22B: confirmed appends only
            "main_written": main_written,    # Phase 24.1: per-tab confirmed
            "data_written": data_written,    # appends (0 when dry/empty/mock)
            "write_outcome": write_outcome,  # SUCCESS|FAILED|UNKNOWN|DRY_RUN|EMPTY|DUPLICATE|SKIPPED|READ_FAILED|RUN_ALREADY_ACTIVE
            "write_error": write_error,
            "sheet_read_ok": sheet_read_ok,  # Phase 22A: False aborts writes
            "run_already_active": run_already_active,  # Phase 22C
            "errors": errors,
            # ── LLM gate metrics (brief §25) ──────────────────────────────────
            "deterministic_candidates": deterministic_candidates,
            "llm_calls": self.verifier.llm_calls,
            "accepted": accepted,
            "review": review,
            "llm_rejected": llm_rejected,
            # ── Phase 24 production-gate accounting (additive; 0 when off) ───
            "production_held": production_held,
            "production_rejected": production_rejected,
            # ── Phase 25.2 routing-assertion accounting (additive) ──────────
            "routing_dropped": routing_dropped,
            # ── Phase 6 enrichment metrics (additive, never overrides above) ──
            "enriched": len(self._enrichment_results),
            "enrichment_ready": sum(1 for e in self._enrichment_results.values()
                                    if getattr(e, "enrichment_status", "") == "READY"),
            "enrichment_ready_without_contact": sum(
                1 for e in self._enrichment_results.values()
                if getattr(e, "enrichment_status", "") == "READY_WITHOUT_CONTACT"),
            "enrichment_review": sum(1 for e in self._enrichment_results.values()
                                     if getattr(e, "enrichment_status", "") == "REVIEW"),
        }

        print(
            f"\nRun Summary\n"
            f"-----------\n"
            f"Scraped:                  {summary['scraped']}\n"
            f"Unique:                   {summary['unique']}\n"
            f"Deterministic candidates: {summary['deterministic_candidates']}\n"
            f"Groq calls:               {summary['llm_calls']}\n"
            f"Accepted:                 {summary['accepted']}\n"
            f"Review:                   {summary['review']}\n"
            f"LLM rejected:             {summary['llm_rejected']}\n"
            f"Deterministic excluded:   {summary['excluded']}\n"
            f"Current-run duplicates:   {summary['current_run_duplicates']}\n"
            f"Existing Sheet duplicates:{summary['sheet_duplicates']}\n"
            f"New rows:                 {summary['new_rows']}\n"
            f"Errors:                   {summary['errors']}"
        )

        return summary


def _enrichment_row(e) -> Dict[str, Any]:
    """Flatten an EnrichedLead into a dict of NEW Phase-6 fields, for reports."""
    return {
        "enrichment_confidence": getattr(e, "enrichment_confidence", 0.0),
        "enrichment_status": getattr(e, "enrichment_status", ""),
        "data_quality_score": getattr(e, "data_quality_score", 0.0),
        "company_confidence": getattr(e, "company_confidence", 0.0),
        "company_evidence": getattr(e, "company_evidence", ""),
        "company_domain": getattr(e, "company_domain", "Not Available"),
        "company_evidence_url": getattr(e, "company_evidence_url", ""),
        "exact_role_confidence": getattr(e, "role_confidence", 0.0),
        "location_confidence": getattr(e, "location_confidence", 0.0),
        "is_remote": getattr(e, "is_remote", False),
        "remote_is_india": getattr(e, "remote_is_india", False),
        "employment_confidence": getattr(e, "employment_confidence", 0.0),
        "experience_confidence": getattr(e, "experience_confidence", 0.0),
        "ctc_confidence": getattr(e, "ctc_confidence", 0.0),
        "hiring_manager_confidence": getattr(e, "hiring_manager_confidence", 0.0),
        "hiring_manager_evidence": getattr(e, "hiring_manager_evidence", ""),
        "hiring_manager_evidence_url": getattr(e, "hiring_manager_evidence_url", ""),
        "email_status": getattr(e, "email_status", ""),
        "email_source": getattr(e, "email_source", ""),
        "email_evidence_url": getattr(e, "email_evidence_url", ""),
        "contact_discovery_status": getattr(e, "contact_discovery_status", ""),
        "evidence_snippets": getattr(e, "evidence_snippets", [])[:6],
        "llm_summary": getattr(e, "llm_summary", ""),
    }
