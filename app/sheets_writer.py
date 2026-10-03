import os
import sys
import time
import traceback
import gspread
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from app.models import ClassifiedPost
from app.llm.schemas import LLM_NON_EVALUATED

# The 23-column layout (19 original txt-spec columns + 4 Phase-16 columns
# APPENDED at the end). Single source of truth: _ensure_headers verifies
# against it and the one-time migration tool writes it.
# BACKWARD COMPATIBILITY: the original 19 columns keep their exact positions
# (notably Source Link stays column 8 for get_existing_urls, Cold Email stays
# column 5 for outreach), so existing readers/index-based consumers are
# unaffected; new columns only extend the row.
EXPECTED_HEADERS = [
    "Company Name",
    "Major Category",
    "Exact Role",
    "CTC",
    "Cold Email",
    "Hiring Manager Name",
    "Hiring Manager LinkedIn",
    "Source Link",
    "Description",
    "Confidence Score",
    "Location",
    "Type",
    "Experience Requirement",
    "Market",
    "India Relevance",
    "Post Date",
    "Scraped At",
    "Classification Reason",
    "Status",
    # ── Phase 16 additions (appended; see §11 data-model spec) ─────────────
    "Hiring Manager Evidence",
    "Apply Google Form",
    "Apply Link",
    "Key Points",
]

def _safe_print(text: str) -> None:
    """Print text safely on any platform.

    A console whose encoding (e.g. cp1252) cannot represent some Unicode chars
    (non-breaking hyphens, emoji, etc.) would otherwise raise UnicodeEncodeError
    and crash the run. We re-encode for the *actual* stdout encoding with
    errors="replace" so nothing is ever lost fatally.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        enc = (getattr(sys.stdout, "encoding", None) or "utf-8")
        safe = text.encode(enc, errors="replace").decode(enc, errors="replace")
        print(safe)

def _with_retry(fn, attempts: int = 4, base_delay: float = 5.0):
    """Retry a Google API call against transient 403/5xx errors."""
    last_error = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e
            print(f"Google Sheets attempt {i + 1}/{attempts} failed: {e}")
            if i < attempts - 1:
                time.sleep(base_delay * (i + 1))
    raise last_error


class SheetReadError(RuntimeError):
    """The existing-URL read failed — NOT the same as an empty Sheet.

    Phase 22A: callers must treat this as READ_FAILURE (abort the write
    phase), never as "there are no existing URLs".
    """


@dataclass
class SheetReadResult:
    """Explicit read outcome so SUCCESSFUL_EMPTY_READ != READ_FAILURE.

    ok=True + urls=set()  -> the Sheet was read and is empty (valid).
    ok=False              -> the read failed; urls is empty and error explains.
    """
    ok: bool
    urls: Set[str] = field(default_factory=set)
    error: str = ""


WRITE_OUTCOMES = ("SUCCESS", "FAILED", "UNKNOWN", "DRY_RUN", "EMPTY",
                    "DUPLICATE", "READ_FAILED")


@dataclass
class WriteResult:
    """Truthful append outcome (Phase 22B/22D).

    written counts ONLY explicitly confirmed rows. On failure written is 0.
    outcome UNKNOWN means the request may or may not have landed (e.g. a
    timeout after the request was sent) — reconcile before retrying, never
    blindly re-append the same batch.
    """
    attempted: int = 0
    written: int = 0
    failed: int = 0
    success: bool = False
    outcome: str = "UNKNOWN"  # one of WRITE_OUTCOMES
    error: str = ""
    # Phase 24.1: confirmed rows per destination tab
    # ({worksheet_title: written}). Empty for single-tab/legacy outcomes.
    destinations: Dict[str, int] = field(default_factory=dict)
    # Phase 24.2: records refused by the production boundary (policy drops,
    # never append failures). Only write_production sets this; write_posts
    # never drops, so it stays 0 there.
    dropped: int = 0
    # Phase 25.2: records dropped by the pre-write tab/category assertion
    # (routing category disagrees with destination, or the same lead would
    # land in both tabs). A subset of `dropped`, reported separately so a
    # systemic routing problem is visible in run summaries. Fail closed.
    routing_dropped: int = 0
    # Phase 32.2: records skipped because their Source Link URL is already
    # present in production (same-batch repeat or already on either tab).
    # A subset of `dropped`, reported separately so idempotent re-calls are
    # visible and never mistaken for new rows. Only write_production sets
    # this; write_posts never dedups, so it stays 0 there.
    duplicates: int = 0


# Substrings marking an append failure as AMBIGUOUS (the request may have
# reached Sheets). Anything else is a definite failure. Never assume
# "exception == no write".
_AMBIGUOUS_WRITE_MARKERS = (
    "timeout", "timed out", "deadline", "ambiguous", "unknown",
    "reset by peer", "connection aborted", "connection reset",
    "temporarily unavailable", "service unavailable",
)


# ── Phase 25.7: Final production write barrier ────────────────────────────
# This is an INDEPENDENT second check layer inside write_production().
# It re-validates every (post, gate) pair against the full production
# invariant AFTER route_production_post() has already approved the record,
# but BEFORE the row is built or appended. A Review/HOLD/REJECT/UNAVAILABLE
# row passed accidentally from any caller is still rejected here.
# Fail closed: any ambiguity or missing field → drop, never write.

_BARRIER_BLOCKED_STATUSES = frozenset({
    "review", "reject", "rejected", "hold", "unavailable",
    "error", "not_run", "malformed", ""
})
_BARRIER_MISSING_VALS = frozenset({
    "unclear", "n/a", "na", "none", "unknown", "",
    "undisclosed", "our client", "confidential", "stealth"
})
_BARRIER_MIN_CONFIDENCE = 0.60


def _production_write_barrier(
    post: Any,
    gate: Any,
    destination: str,
    category: str,
) -> tuple:
    """Final production write barrier (Phase 25.7). Independent of upstream routing.

    Returns (allowed: bool, reason: str). Fail closed: any uncertainty → block.
    Never raises; exceptions are treated as a block.

    Checks (independently, without trusting caller):
      1. destination is a production tab (MAIN_PRODUCTION or DATA_PRODUCTION)
      2. post.status is NOT Review/HOLD/REJECT/UNAVAILABLE/ERROR/empty
      3. gate.confidence >= 0.60
      4. gate.llm_status is an evaluated (non-null) status
      5. post.company_name is not Unclear/missing
      6. post.hiring_manager_name is not Unclear/missing
      7. post.exact_role is not Unclear/missing
      8. destination matches the category (DA/DS only in DATA, FO/CoS only in MAIN)
    """
    try:
        from app.production_gate import (
            DATA_PRODUCTION, MAIN_PRODUCTION,
            DATA_CATEGORIES, MAIN_PRODUCTION_CATEGORIES,
        )

        # 1. Must be a production destination
        if destination not in (MAIN_PRODUCTION, DATA_PRODUCTION):
            return False, f"barrier: non-production destination {destination!r}"

        # 2. Status must NOT be a blocked value
        status = (getattr(post, "status", "") or "").strip().lower()
        if status in _BARRIER_BLOCKED_STATUSES:
            return False, f"barrier: post.status={status!r} is blocked (Review/HOLD/REJECT/UNAVAILABLE)"

        # 3. Confidence floor (independent parse — do not trust route)
        try:
            confidence = float(getattr(gate, "confidence", 0) or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < _BARRIER_MIN_CONFIDENCE:
            return False, f"barrier: confidence {confidence:.3f} < 0.60"

        # 4. LLM must have evaluated
        llm_status = getattr(gate, "llm_status", "ERROR") or "ERROR"
        if llm_status in LLM_NON_EVALUATED:
            return False, f"barrier: LLM not evaluated (llm_status={llm_status!r})"

        # 5. Company not missing/unclear
        company = (getattr(post, "company_name", "") or "").strip().lower()
        if company in _BARRIER_MISSING_VALS:
            return False, f"barrier: company_name={company!r} is missing/unclear"

        # 6. Hiring manager not missing/unclear
        hm = (getattr(post, "hiring_manager_name", "") or "").strip().lower()
        if hm in _BARRIER_MISSING_VALS:
            return False, f"barrier: hiring_manager_name={hm!r} is missing/unclear"

        # 7. Exact role not missing/unclear
        role = (getattr(post, "exact_role", "") or "").strip().lower()
        if role in _BARRIER_MISSING_VALS:
            return False, f"barrier: exact_role={role!r} is missing/unclear"

        # 8. Category must match destination
        if destination == DATA_PRODUCTION and (category or "") not in DATA_CATEGORIES:
            return False, f"barrier: category {category!r} not valid for DATA_PRODUCTION"
        if destination == MAIN_PRODUCTION and (category or "") not in MAIN_PRODUCTION_CATEGORIES:
            return False, f"barrier: category {category!r} not valid for MAIN_PRODUCTION"

        return True, "barrier passed"

    except Exception as exc:  # fail closed — never trust partial barrier results
        return False, f"barrier: internal error ({type(exc).__name__}); held"



def _sanitize_write_error(text) -> str:
    """Single-line, capped error text for run summaries (never credentials)."""
    s = " ".join(str(text or "").split())
    return s[:300]


def _classify_write_error(exc: Exception) -> str:
    """FAILED (definite no-write evidence) vs UNKNOWN (may have landed)."""
    blob = " ".join(str(exc or "").split()).lower()
    if any(m in blob for m in _AMBIGUOUS_WRITE_MARKERS):
        return "UNKNOWN"
    return "FAILED"

class SheetsWriter:
    def __init__(self, credentials_path: str, sheet_id: str, worksheet_name: str, dry_run: bool = True,
                 data_worksheet_name: str | None = None):
        self.credentials_path = credentials_path
        self.sheet_id = sheet_id
        self.worksheet_name = worksheet_name
        # Phase 24.1: second production tab (data roles) in the SAME
        # spreadsheet. None (default) preserves single-tab behavior exactly.
        self.data_worksheet_name = data_worksheet_name
        self.dry_run = dry_run
        self.client = None
        self.worksheet = None
        self.data_worksheet = None

        # Phase 25.6A: on TLS-inspecting networks the enterprise gateway CA
        # lives in the OS store but not Mozilla's bundle. Select OS-backed
        # trust anchors for this process (verification stays fully on).
        # Never raises; without a gateway CA this is a silent no-op.
        try:
            from app.tls_trust import ensure_google_trust
            ensure_google_trust()
        except Exception:
            pass

        if os.path.exists(self.credentials_path):
            try:
                self.client = _with_retry(lambda: gspread.service_account(filename=self.credentials_path))
                sheet = _with_retry(lambda: self.client.open_by_key(self.sheet_id))
                self.worksheet = self._open_tab(sheet, self.worksheet_name)
                if self.data_worksheet_name:
                    self.data_worksheet = self._open_tab(sheet, self.data_worksheet_name)
                if self.worksheet is not None:
                    self._ensure_headers_for(self.worksheet, self.worksheet_name)
                if self._data_ws() is not None:
                    self._ensure_headers_for(self._data_ws(), self.data_worksheet_name)
            except Exception as e:
                print(f"Failed to authenticate or access Google Sheets: {e}")
                traceback.print_exc()
                self.dry_run = True
        else:
            print("WARNING: Google Sheets credentials not found. Forcing DRY_RUN.")
            self.dry_run = True

    def _open_tab(self, sheet, name: str):
        """Open a tab, creating it (live mode only) when missing.

        Phase 24.1 creation path mirrors the long-standing main-tab behavior:
        identical row/col sizing and header verification. In DRY_RUN a missing
        tab stays None and is never created (no live mutation).
        """
        try:
            ws = sheet.worksheet(name)
            print(f"Found existing worksheet '{name}'.")
            return ws
        except gspread.exceptions.WorksheetNotFound:
            if self.dry_run:
                print(f"Worksheet '{name}' not found. It will be created during production write (DRY_RUN=false).")
                return None
            ws = sheet.add_worksheet(
                title=name, rows="1000",
                cols=str(len(EXPECTED_HEADERS)))
            print(f"Created worksheet '{name}'.")
            return ws

    def _data_ws(self):
        """Data tab or None (getattr-safe for pre-24.1 constructed writers)."""
        return getattr(self, "data_worksheet", None)

    def _ensure_headers(self):
        self._ensure_headers_for(self.worksheet, self.worksheet_name)

    def _ensure_headers_for(self, ws, name: str):
        expected_headers = EXPECTED_HEADERS
        try:
            headers = ws.row_values(1)
        except Exception as e:
            print(f"Error reading headers: {e}")
            headers = []

        if not headers:
            if self.dry_run:
                print("Sheet is empty. Headers will be created during production write (DRY_RUN=false).")
            else:
                try:
                    ws.append_row(expected_headers)
                    print(f"Created header row in Google Sheet ('{name}').")
                except Exception as e:
                    print(f"Error creating headers: {e}")
        else:
            if headers != expected_headers:
                print(f"CRITICAL ERROR: Existing headers do not match expected schema.")
                print(f"Expected: {expected_headers}")
                print(f"Found: {headers}")
                if not self.dry_run:
                    raise ValueError("Header schema mismatch. Please update or clear the worksheet headers before a live run.")
            else:
                print("Existing headers match expected schema.")

    def _column_urls(self, ws) -> Set[str]:
        """Source Link URLs (column 8, header skipped) from one tab."""
        urls = ws.col_values(8)
        if not isinstance(urls, list):
            raise SheetReadError(
                f"Malformed Sheet response: col_values(8) returned "
                f"{type(urls).__name__}, expected list."
            )
        return set(urls[1:])  # Skip header row

    def get_existing_urls(self) -> Set[str]:
        """Return existing Source Link URLs (column 8, header skipped).

        Phase 22A fail-closed: a SUCCESSFUL read returns the URL set (possibly
        EMPTY — an empty Sheet is valid). A FAILED read RAISES SheetReadError
        and must abort the write phase — it must never be read as "no URLs".

        Phase 24.1: the read unions BOTH production tabs, so a URL present in
        either tab blocks a duplicate production write. A failure on either
        tab fails the whole read closed.
        """
        if self.dry_run or not self.worksheet:
            return set()

        try:
            urls = self._column_urls(self.worksheet)
            data_ws = self._data_ws()
            if data_ws is not None:
                urls |= self._column_urls(data_ws)
            return urls
        except SheetReadError:
            raise
        except Exception as e:
            print(f"Error fetching existing URLs: {e}")
            raise SheetReadError(f"Sheet URL read failed: {e}") from e

    def try_read_existing_urls(self) -> SheetReadResult:
        """Non-raising read: SUCCESSFUL_EMPTY_READ vs READ_FAILURE explicit."""
        if self.dry_run or not self.worksheet:
            return SheetReadResult(ok=True, urls=set())
        try:
            return SheetReadResult(ok=True, urls=self.get_existing_urls())
        except SheetReadError as e:
            return SheetReadResult(ok=False, urls=set(), error=str(e))
        except Exception as e:  # belt-and-braces; fail closed
            return SheetReadResult(
                ok=False, urls=set(), error=f"Sheet URL read failed: {e}"
            )

    @staticmethod
    def _build_row(post: ClassifiedPost, category_override: str | None = None) -> list:
        """One 23-cell row in EXPECTED_HEADERS order (shared by both tabs).

        Phase 25.2: `category_override` stamps the row's category cell with
        the AUTHORITATIVE routing category (final_category). The merged lead
        normally already carries it (enriched_post folds the evaluated
        verdict), but the override guarantees the printed cell can never
        disagree with the tab the row lands in. Legacy write_posts passes
        nothing and behaves byte-identically.
        """
        return [
            post.company_name,
            category_override if category_override is not None else post.major_category,
            post.exact_role,
            post.ctc,
            post.cold_email,
            post.hiring_manager_name,
            post.hiring_manager_linkedin,
            post.source_link,
            post.description,
            post.confidence,
            post.location,
            post.employment_type,
            post.experience_requirement,
            post.market,
            post.india_relevance,
            post.post_date,
            post.scraped_at,
            post.classification_reason,
            post.status,
            # Phase 16 (getattr-guarded so legacy-shaped objects degrade
            # to empty cells instead of crashing the write).
            getattr(post, "hiring_manager_evidence", ""),
            getattr(post, "apply_google_form", ""),
            getattr(post, "apply_link", ""),
            getattr(post, "key_points", ""),
        ]

    def _split_by_tab(self, posts: List[ClassifiedPost]):
        """Split a batch into (main_posts, data_posts) by validated category.

        Phase 24.1 mutually exclusive routing: Data Analyst / Data Scientist
        go to the data tab, every other category to the main tab. When no data
        tab is configured the whole batch stays on the main tab (pre-24.1
        behavior preserved byte-for-byte). The writer never drops rows;
        eligibility is the gate's job, not the writer's.
        """
        if self._data_ws() is None:
            return list(posts), []
        from app.production_gate import DATA_PRODUCTION, route_category
        main, data = [], []
        for post in posts:
            dest = route_category(getattr(post, "major_category", ""))
            if dest == DATA_PRODUCTION:
                data.append(post)
            else:
                main.append(post)
        return main, data

    def _append_batch(self, ws, rows: list) -> WriteResult:
        """One tab's append: single attempt, no auto-retry (Phase 22D)."""
        try:
            ws.append_rows(rows, value_input_option="USER_ENTERED")
            print(f"Successfully wrote {len(rows)} rows to Google Sheets.")
            return WriteResult(attempted=len(rows), written=len(rows),
                               failed=0, success=True, outcome="SUCCESS")
        except Exception as e:
            outcome = _classify_write_error(e)
            print(f"Failed to write to Google Sheets ({outcome}): {e}")
            return WriteResult(attempted=len(rows), written=0,
                               failed=len(rows), success=False,
                               outcome=outcome,
                               error=_sanitize_write_error(e))

    def write_production(
        self, records: List[Tuple[ClassifiedPost, Any]]) -> WriteResult:
        """Append ONLY production-eligible records (Phase 24.2 boundary).

        `records` are (post, gate) pairs where `gate` is the GateResult from
        app.llm.schemas. Every pair is re-validated here through
        app.production_gate.route_production_post — which runs the full
        production gate BEFORE routing — and only ELIGIBLE records are
        appended, to exactly one of the two production tabs by validated
        category (Data Analyst / Data Scientist → data tab, everything else
        supported → main tab).

        Fail-closed second layer (the orchestrator pre-filters, this
        re-checks; Sec 4/5):
          - REVIEW / HOLD / REJECT / UNAVAILABLE / ERROR / NOT_RUN /
            MALFORMED records → dropped, never appended.
          - confidence null or < 0.60 → dropped (status + confidence are
            checked explicitly; 0.00 is never treated as a score).
          - Company/HM unclear, unverified, or unresolvable → dropped.
          - JOB_ADVERTISING_PAGE / AGGREGATOR / UNKNOWN / weak-agency
            posters → dropped.
          - vague/multi-role, India-gate failure, >600-word description,
            unroutable category → dropped.
          - a routing exception on one record drops that record only.

        Dropped records are counted in WriteResult.dropped; they are never
        failures (failed stays 0 for policy drops) and never retried here —
        they belong in internal review artifacts, not production tabs.
        DRY_RUN / empty / all-dropped inputs never touch the API.

        Phase 32.2 IDEMPOTENT WRITE: before any append, the existing Source
        Link URLs are re-read across BOTH production tabs fail-closed and
        any already-present URL is skipped (counted in WriteResult.duplicates;
        existing rows preserved). A call with nothing new to append returns
        DUPLICATE (written=0) — never misreported as a new row. An unreadable
        Sheet aborts closed (READ_FAILED). This is the single source of truth
        shared by the orchestrator, recovery reruns, and direct callers.
        """
        from app.production_gate import (
            DATA_CATEGORIES,
            DATA_PRODUCTION,
            MAIN_PRODUCTION,
            MAIN_PRODUCTION_CATEGORIES,
            route_production_post,
        )
        if not records:
            return WriteResult(attempted=0, written=0, failed=0,
                                success=True, outcome="EMPTY")
        total = len(records)
        main_rows: list = []
        data_rows: list = []
        main_urls: Set[str] = set()
        data_urls: Set[str] = set()
        seen_urls: Set[str] = set()  # batch-level: one lead, one tab, once
        dropped = 0
        routing_dropped = 0
        duplicates = 0  # Phase 32.2: sheet/batch duplicate skips
        routing_errors: List[str] = []
        for post, gate in records:
            try:
                route = route_production_post(post, gate)
            except Exception:
                dropped += 1  # fail closed: unroutable ⇒ never production
                continue
            if route.destination == MAIN_PRODUCTION:
                dest, bucket, bucket_urls = (
                    MAIN_PRODUCTION, main_rows, main_urls)
                ok = (route.category or "") in MAIN_PRODUCTION_CATEGORIES
            elif route.destination == DATA_PRODUCTION:
                dest, bucket, bucket_urls = (
                    DATA_PRODUCTION, data_rows, data_urls)
                ok = (route.category or "") in DATA_CATEGORIES
            else:  # HOLD / REJECT: internal review material, not production
                dropped += 1
                continue
            # Phase 25.2 HARD pre-write assertion (§7): the authoritative
            # routing category must agree with the destination tab, and the
            # same lead must never land in both tabs. Violation ⇒ drop the
            # record (fail closed), never append it anywhere.
            url = getattr(post, "source_link", "") or ""
            if not ok:
                dropped += 1
                routing_dropped += 1
                routing_errors.append(
                    "ROUTING_ERROR: category %r incompatible with %s (%s)"
                    % (route.category, dest, url[:80]))
                continue
            if url and url in seen_urls:
                dropped += 1  # same lead twice in one batch ⇒ keep first only
                duplicates += 1  # Phase 32.2: same-batch repeat, counted
                continue
            if url:
                seen_urls.add(url)
            # Phase 25.7 FINAL WRITE BARRIER: independent second-layer check.
            # This re-validates the (post, gate) pair against the full
            # production invariant WITHOUT trusting the upstream route result.
            # A Review/HOLD/REJECT/UNAVAILABLE record passed accidentally from
            # any caller — including a misconfigured orchestrator or a test
            # that bypassed the orchestrator entirely — is blocked here.
            # Fail closed: any uncertainty or exception → drop, never write.
            _barrier_ok, _barrier_reason = _production_write_barrier(
                post, gate, dest, route.category or "")
            if not _barrier_ok:
                dropped += 1
                routing_dropped += 1
                routing_errors.append(
                    "WRITE_BARRIER_DROP: %s (url=%s)" % (_barrier_reason, url[:80]))
                continue
            # Stamp the row's category cell with the authoritative routing
            # category so the printed cell can never disagree with its tab.
            bucket.append(self._build_row(post, category_override=route.category))
            bucket_urls.add(url)
        # Cross-tab exclusivity is structural (single destination per record),
        # but assert it anyway: no URL may sit in both batches.
        _overlap = main_urls & data_urls
        if _overlap:
            dropped += len(_overlap)
            routing_dropped += len(_overlap)
            routing_errors.append(
                "ROUTING_ERROR: %d lead(s) routed to BOTH tabs; dropped"
                % len(_overlap))
            main_rows = [r for r in main_rows if r[7] not in _overlap]
            data_rows = [r for r in data_rows if r[7] not in _overlap]

        if self.dry_run:
            total_rows = len(main_rows) + len(data_rows)
            main_name = getattr(self, "worksheet_name", "LinkedIn Hiring Leads")
            data_name = getattr(self, "data_worksheet_name", None)
            _safe_print(f"[DRY RUN] Would write {len(main_rows)} production rows to "
                        f"'{main_name}'"
                        + (f" and {len(data_rows)} production rows to "
                           f"'{data_name}'." if data_rows else ".")
                        + f" ({dropped} non-eligible dropped.)")
            for r in main_rows + data_rows:
                _safe_print(str(r))
            return WriteResult(attempted=total, written=0, failed=0,
                               success=True, outcome="DRY_RUN", dropped=dropped,
                               routing_dropped=routing_dropped,
                               duplicates=duplicates,
                               error="; ".join(routing_errors))

        # ── Phase 32.2 IDEMPOTENT PRODUCTION WRITE ─────────────────────────
        # Single source of truth shared by the orchestrator, recovery
        # runners, and every direct caller: before ANY append, re-read the
        # existing Source Link URLs across BOTH production tabs
        # fail-closed and skip every already-present URL. The orchestrator
        # pre-filter alone is insufficient — direct write_production callers
        # (recovery reruns, repeated gate passes, natural-completion
        # re-gates) bypass it, which caused the Phase 32.1 Microsoft
        # double-write. An ambiguous read aborts the whole write closed
        # (READ_FAILED, written=0), never "no URLs". A call whose rows are
        # ALL already present returns DUPLICATE (written=0) — never
        # reported as a successful new row. Unreachable in DRY_RUN (which
        # returns above without touching the API).
        url_read = self.try_read_existing_urls()
        if not url_read.ok:
            return WriteResult(
                attempted=total, written=0, failed=0, success=False,
                outcome="READ_FAILED", dropped=dropped,
                routing_dropped=routing_dropped, duplicates=duplicates,
                error=("Sheet URL read failed; production write aborted "
                       "fail-closed: %s" % (url_read.error or "unknown")))
        existing_urls = set(url_read.urls or set())

        def _already_present(row: list) -> bool:
            url = row[7] if len(row) > 7 else ""
            return bool(url) and url in existing_urls

        kept_main = [r for r in main_rows if not _already_present(r)]
        kept_data = [r for r in data_rows if not _already_present(r)]
        sheet_dupes = (len(main_rows) - len(kept_main)
                       + len(data_rows) - len(kept_data))
        if sheet_dupes:
            duplicates += sheet_dupes
            dropped += sheet_dupes
            routing_errors.append(
                "%d duplicate URL(s) already in production; skipped, "
                "existing row(s) preserved" % sheet_dupes)
        main_rows, data_rows = kept_main, kept_data

        if duplicates and not main_rows and not data_rows:
            return WriteResult(
                attempted=total, written=0, failed=0, success=True,
                outcome="DUPLICATE", dropped=dropped,
                routing_dropped=routing_dropped, duplicates=duplicates,
                error="; ".join(routing_errors))

        if not main_rows and not data_rows:
            return WriteResult(attempted=total, written=0, failed=0,
                               success=True, outcome="EMPTY", dropped=dropped,
                               routing_dropped=routing_dropped,
                               duplicates=duplicates,
                               error="; ".join(routing_errors))

        # Phase 22D: single append attempt per tab, no auto-retry.
        results = []
        dests: Dict[str, int] = {}
        data_ws = self._data_ws()
        main_name = getattr(self, "worksheet_name", "LinkedIn Hiring Leads")
        data_name = getattr(self, "data_worksheet_name", None)
        if main_rows:
            if self.worksheet is None:
                results.append(WriteResult(
                    attempted=len(main_rows), written=0, failed=len(main_rows),
                    success=False, outcome="FAILED",
                    error="main worksheet unavailable"))
            else:
                r = self._append_batch(self.worksheet, main_rows)
                results.append(r)
                if r.written:
                    dests[main_name] = r.written
        if data_rows:
            if data_ws is None:  # fail closed: never spill data rows elsewhere
                results.append(WriteResult(
                    attempted=len(data_rows), written=0, failed=len(data_rows),
                    success=False, outcome="FAILED",
                    error="data worksheet unavailable"))
            else:
                r = self._append_batch(data_ws, data_rows)
                results.append(r)
                if r.written:
                    dests[data_name or "Data Analyst & Data Scientist"] = r.written

        attempted = sum(r.attempted for r in results)
        written = sum(r.written for r in results)
        failed = sum(r.failed for r in results)
        errors = "; ".join(r.error for r in results if r.error)
        if routing_errors:
            # Routing violations are systemic signals: surface them loudly
            # alongside append outcomes (records were already dropped closed).
            errors = "; ".join([*routing_errors, errors] if errors
                               else routing_errors)
        outcomes = [r.outcome for r in results]
        if all(o == "SUCCESS" for o in outcomes):
            outcome = "SUCCESS"
        elif any(o == "UNKNOWN" for o in outcomes):
            outcome = "UNKNOWN"
        else:
            outcome = "FAILED"
        return WriteResult(attempted=total, written=written, failed=failed,
                           success=(outcome == "SUCCESS"), outcome=outcome,
                           error=errors, destinations=dests, dropped=dropped,
                           routing_dropped=routing_dropped,
                           duplicates=duplicates)

    def write_posts(self, posts: List[ClassifiedPost]) -> WriteResult:
        """Append rows; return a truthful WriteResult (Phase 22B/22D).

        PHASE 25.7 QUARANTINE NOTICE: This method performs NO production
        eligibility filtering. It must NEVER be used for production writes
        when the Phase-24 gate is enabled. All production traffic must flow
        through write_production(), which runs the full gate + the Phase 25.7
        final write barrier. write_posts() is retained exclusively for:
          - DRY_RUN validation / analysis exports
          - Legacy / test callers that have explicitly set
            ALLOW_UNGATED_PRODUCTION_WRITE=1 or dry_run=True
        A live (non-dry-run) call without the ungated override is blocked
        by the orchestrator's Phase 25.1 invocation guard before it ever
        reaches here; this method adds its own fail-closed live guard as a
        secondary layer.

        - DRY_RUN / empty input never touches the API (written=0, success=True).
        - Live success: written == attempted (the only confirmed count).
        - Live failure: written == 0; outcome FAILED (definite) or UNKNOWN
          (ambiguous — may have landed; reconcile before retrying).
        - The append itself is NEVER auto-retried: re-appending after an
          ambiguous outcome could duplicate rows. The caller must reconcile.
        - Phase 24.1: the batch is split by validated category across the two
          production tabs (same row mapping, only the destination differs).
          Outcomes aggregate truthfully: any UNKNOWN poisons the total to
          UNKNOWN; otherwise any FAILED fails it. Per-tab confirmed counts go
          in `destinations`.
        - Phase 24.2: this method performs NO eligibility filtering (frozen
          by Phase 22 pins). Production traffic must use write_production,
          which re-validates every record through the production gate.
        """
        if not posts:
            return WriteResult(attempted=0, written=0, failed=0,
                               success=True, outcome="EMPTY")

        # Phase 25.7 live-write guard: if this writer is live (not dry-run)
        # and the caller has not opted in with ALLOW_UNGATED_PRODUCTION_WRITE,
        # block immediately. The orchestrator's Phase 25.1 guard should have
        # caught this first; this is a secondary fail-closed layer.
        if not self.dry_run:
            from app.config import ALLOW_UNGATED_PRODUCTION_WRITE
            if not ALLOW_UNGATED_PRODUCTION_WRITE:
                raise RuntimeError(
                    "write_posts() called with a live writer but "
                    "ALLOW_UNGATED_PRODUCTION_WRITE is not set. "
                    "Production writes must use write_production(). "
                    "Set ALLOW_UNGATED_PRODUCTION_WRITE=1 only for explicit "
                    "legacy maintenance; never for normal production runs."
                )

        main_posts, data_posts = self._split_by_tab(posts)
        rows_main = [self._build_row(p) for p in main_posts]
        rows_data = [self._build_row(p) for p in data_posts]

        if self.dry_run:
            total = len(rows_main) + len(rows_data)
            main_name = getattr(self, "worksheet_name", "LinkedIn Hiring Leads")
            data_name = getattr(self, "data_worksheet_name", None)
            _safe_print(f"[DRY RUN] Would write {len(rows_main)} rows to "
                        f"'{main_name}'"
                        + (f" and {len(rows_data)} rows to "
                           f"'{data_name}'." if rows_data else "."))
            for r in rows_main + rows_data:
                _safe_print(str(r))
            return WriteResult(attempted=total, written=0, failed=0,
                               success=True, outcome="DRY_RUN")

        # Phase 22D: single append attempt per tab, no auto-retry.
        results = []
        dests: Dict[str, int] = {}
        data_ws = self._data_ws()
        main_name = getattr(self, "worksheet_name", "LinkedIn Hiring Leads")
        data_name = getattr(self, "data_worksheet_name", None)
        if rows_main:
            if self.worksheet is None:
                results.append(WriteResult(
                    attempted=len(rows_main), written=0, failed=len(rows_main),
                    success=False, outcome="FAILED",
                    error="main worksheet unavailable"))
            else:
                r = self._append_batch(self.worksheet, rows_main)
                results.append(r)
                if r.written:
                    dests[main_name] = r.written
        if rows_data:
            if data_ws is None:  # must not happen (split guards), fail closed
                results.append(WriteResult(
                    attempted=len(rows_data), written=0, failed=len(rows_data),
                    success=False, outcome="FAILED",
                    error="data worksheet unavailable"))
            else:
                r = self._append_batch(data_ws, rows_data)
                results.append(r)
                if r.written:
                    dests[data_name or "Data Analyst & Data Scientist"] = r.written

        attempted = sum(r.attempted for r in results)
        written = sum(r.written for r in results)
        failed = sum(r.failed for r in results)
        errors = "; ".join(r.error for r in results if r.error)
        outcomes = [r.outcome for r in results]
        if all(o == "SUCCESS" for o in outcomes):
            outcome = "SUCCESS"
        elif any(o == "UNKNOWN" for o in outcomes):
            outcome = "UNKNOWN"
        else:
            outcome = "FAILED"
        return WriteResult(attempted=attempted, written=written, failed=failed,
                           success=(outcome == "SUCCESS"), outcome=outcome,
                           error=errors, destinations=dests)
