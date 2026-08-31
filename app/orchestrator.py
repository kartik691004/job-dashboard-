from typing import Dict, Any, List

from app.sources.datadoping_source import DataDopingSource
from app.classifier import DeterministicClassifier
from app.sheets_writer import SheetsWriter
from app.config import (
    APIFY_API_TOKEN,
    GOOGLE_SHEETS_CREDENTIALS,
    GOOGLE_SHEETS_ID,
    GOOGLE_SHEET_WORKSHEET,
    DRY_RUN,
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

class Orchestrator:
    def __init__(self):
        # datadoping/linkedin-posts-search-scraper replaced supreme_coder/linkedin-post,
        # whose content search has returned "No posts found" for every query since
        # 2026-08-23. This actor takes keywords directly and covers all of them in ONE
        # run, so a pipeline execution costs one run instead of one-per-query.
        self.source = DataDopingSource(APIFY_API_TOKEN)
        self.classifier = DeterministicClassifier()
        self.sheets_writer = SheetsWriter(GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID, GOOGLE_SHEET_WORKSHEET, DRY_RUN)
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

        # 4. Classify (deterministic pre-filter) + LLM quality gate.
        # Only deterministic-passing candidates are sent to the LLM — that is
        # the cost-control boundary (brief §25).
        accepted_posts: List = []
        review_posts: List = []
        excluded_count = 0
        deterministic_candidates = 0
        accepted = 0
        review = 0
        llm_rejected = 0

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
                "post_date": merged.post_date,
                "description": merged.description,
            }
            if gate.decision == "ACCEPT":
                accepted += 1
                accepted_posts.append(merged)
                # Phase 6: enrich the ACCEPTED lead (adds completeness/evidence
                # without changing the vacancy decision). Stored keyed by source.
                try:
                    enriched = self.enricher.enrich_from_classified(merged, gate.verdict)
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

        valid_posts = accepted_posts + review_posts

        # 5. Level 2 Deduplication — Google Sheet
        try:
            existing_urls = self.sheets_writer.get_existing_urls()
        except Exception as e:
            print(f"Error reading Sheet URLs: {e}")
            errors += 1
            existing_urls = set()

        new_valid_posts = []
        sheet_duplicates = 0
        for p in valid_posts:
            if p.source_link in existing_urls:
                sheet_duplicates += 1
            else:
                new_valid_posts.append(p)

        # 6. Write to Sheets (skipped when DRY_RUN=True). ACCEPT rows carry
        # Status=New, REVIEW rows Status=Review — never mixed, never auto-trusted.
        if new_valid_posts:
            try:
                self.sheets_writer.write_posts(new_valid_posts)
            except Exception as e:
                print(f"Sheet write error: {e}")
                errors += 1

        # 7. Summary
        summary = {
            "scraped": len(raw_posts),
            "unique": len(unique_raw_posts),
            "valid": accepted,               # confirmed leads only
            "excluded": excluded_count,
            "current_run_duplicates": current_run_duplicates,
            "sheet_duplicates": sheet_duplicates,
            "new_rows": len(new_valid_posts),  # New + Review rows actually written
            "errors": errors,
            # ── LLM gate metrics (brief §25) ──────────────────────────────────
            "deterministic_candidates": deterministic_candidates,
            "llm_calls": self.verifier.llm_calls,
            "accepted": accepted,
            "review": review,
            "llm_rejected": llm_rejected,
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
