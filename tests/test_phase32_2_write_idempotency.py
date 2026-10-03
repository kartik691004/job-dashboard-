"""Phase 32.2 — production-write idempotency regression tests.

Proves that SheetsWriter.write_production() is idempotent by stable source
URL: an already-written production URL can never be appended again — across
repeated calls, recovery re-calls, natural-completion re-gates, tabs, or
within one batch. The protection lives at the FINAL write boundary (the
writer reads existing URLs across BOTH tabs fail-closed), not only in the
orchestrator pre-filter that direct recovery callers bypass (Phase 32.1
Microsoft double-write).

All tests offline (no Apify, no LLM, no network, no real Sheets).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.schemas import GateResult, LeadVerdict
from app.models import ClassifiedPost
from app.production_gate import route_production_post, DATA_PRODUCTION
from app.sheets_writer import SheetsWriter, WriteResult


# ── fixtures (established FakeWS / eligible-pair patterns) ────────────────

class FakeWS:
    """In-memory worksheet double: pre-seedable URL column + append log."""
    def __init__(self, urls=None, fail_read_with=None):
        self._urls = list(urls or [])
        self.appended = []
        self.append_calls = 0
        self.fail_read_with = fail_read_with

    def col_values(self, n):
        if self.fail_read_with is not None:
            raise self.fail_read_with
        return ["Source Link"] + list(self._urls)

    def append_rows(self, rows, value_input_option=None):
        self.append_calls += 1
        self.appended.extend(rows)
        # NOTE: the double deliberately does NOT auto-extend _urls, so a
        # writer that fails to consult the sheet keeps double-writing while
        # the test observes every append. Sheet state is modelled by _urls.


def two_tab_writer(main_urls=None, data_urls=None,
                   fail_main_read=None, fail_data_read=None):
    w = SheetsWriter.__new__(SheetsWriter)
    w.dry_run = False
    w.credentials_path = "mock"
    w.sheet_id = "mock"
    w.worksheet_name = "LinkedIn Hiring Leads"
    w.data_worksheet_name = "Data Analyst & Data Scientist"
    w.worksheet = FakeWS(urls=main_urls, fail_read_with=fail_main_read)
    w.data_worksheet = FakeWS(urls=data_urls, fail_read_with=fail_data_read)
    return w


def eligible_pair(url, category="Founder's Office", role="Chief of Staff"):
    text = ("Acme is hiring a %s in Bangalore. Full-time role. "
            "DM Rohan Mehta to apply." % role)
    m = ClassifiedPost(
        post_url=url, post_date="2026-09-14", text=text, author_name="Acme",
        author_profile_url="https://www.linkedin.com/company/acme",
        company_name="Acme", major_category=category, exact_role=role,
        hiring_manager_name="Rohan Mehta",
        hiring_manager_evidence="named in post: Rohan Mehta",
        hiring_manager_linkedin="Unclear",
        location="Bangalore", employment_type="Full-time",
        experience_requirement="2-4 Years", india_relevance="India",
        market="India", source_link=url, description=text[:400],
        key_points="", confidence=0.95, scraped_at="now",
        classification_reason="ACCEPT: test", status="New",
        job_card_company="Unclear", is_valid=True,
    )
    verdict = LeadVerdict(
        decision="ACCEPT", confidence=0.95,
        is_current_job=True, is_genuine_hiring=True, is_target_role=True,
        major_category=category, india_relevance="India",
        employment_type="Full-time", company="Acme",
        exact_role=role, reason="test verdict",
    )
    g = GateResult(verdict=verdict, decision="ACCEPT", status="New",
                   llm_status="SUCCESS")
    return m, g


DA_URL = "https://www.linkedin.com/posts/acme-da-activity-1"
FO_URL = "https://www.linkedin.com/posts/acme-fo-activity-2"
OTHER_URL = "https://www.linkedin.com/posts/acme-fo-activity-3"


# ── A. write once → success ───────────────────────────────────────────────

def test_a_first_write_succeeds():
    w = two_tab_writer()
    m, g = eligible_pair(FO_URL)
    r = w.write_production([(m, g)])
    assert r.outcome == "SUCCESS" and r.written == 1 and r.success is True
    assert len(w.worksheet.appended) == 1
    assert w.worksheet.appended[0][7] == FO_URL
    assert r.duplicates == 0


# ── B. write identical candidate twice → exactly one row ──────────────────

def test_b_second_identical_write_appends_nothing():
    w = two_tab_writer()
    m, g = eligible_pair(FO_URL)
    r1 = w.write_production([(m, g)])
    assert r1.written == 1
    # The sheet now contains the URL (as a real second read would see).
    w.worksheet._urls.append(FO_URL)
    r2 = w.write_production([(m, g)])
    assert r2.written == 0, "second write must append nothing"
    assert r2.duplicates == 1
    assert r2.outcome == "DUPLICATE", r2
    assert len(w.worksheet.appended) == 1, "exactly one sheet row total"


# ── C. recovery re-call of an already-written ACCEPT → zero new rows ──────

def test_c_recovery_recall_of_written_accept_writes_nothing():
    # Sheet state as left by an earlier completed window.
    w = two_tab_writer(main_urls=[FO_URL])
    m, g = eligible_pair(FO_URL)  # same ACCEPT re-gated from checkpoint
    r = w.write_production([(m, g)])
    assert r.written == 0 and r.duplicates == 1
    assert r.outcome == "DUPLICATE"
    assert w.worksheet.appended == [] and w.data_worksheet.appended == []


# ── D. natural-completion re-gate cannot duplicate ────────────────────────

def test_d_regate_then_rewrite_is_idempotent():
    w = two_tab_writer()
    m, g = eligible_pair(DA_URL, "Data Scientist", "Data Scientist")
    assert route_production_post(m, g).destination == DATA_PRODUCTION
    r1 = w.write_production([(m, g)])
    assert r1.written == 1
    w.data_worksheet._urls.append(DA_URL)
    # Natural completion re-gates the same stored ACCEPT and re-calls.
    assert route_production_post(m, g).destination == DATA_PRODUCTION
    r2 = w.write_production([(m, g)])
    assert r2.written == 0 and r2.outcome == "DUPLICATE"
    assert len(w.data_worksheet.appended) == 1


# ── E. MAIN vs DATA cross-tab duplicate blocked ───────────────────────────

def test_e_url_in_main_blocks_data_write_and_vice_versa():
    w = two_tab_writer(main_urls=[FO_URL])
    m, g = eligible_pair(FO_URL)
    r = w.write_production([(m, g)])
    assert r.written == 0 and r.duplicates == 1 and r.outcome == "DUPLICATE"
    assert w.worksheet.appended == []

    w2 = two_tab_writer(data_urls=[DA_URL])
    m2, g2 = eligible_pair(DA_URL, "Data Scientist", "Data Scientist")
    r2 = w2.write_production([(m2, g2)])
    assert r2.written == 0 and r2.duplicates == 1 and r2.outcome == "DUPLICATE"
    assert w2.data_worksheet.appended == []


# ── F. duplicate URL within the same batch blocked ────────────────────────

def test_f_same_batch_duplicate_written_once():
    w = two_tab_writer()
    m, g = eligible_pair(FO_URL)
    m2, g2 = eligible_pair(FO_URL)
    r = w.write_production([(m, g), (m2, g2)])
    assert r.written == 1
    assert r.duplicates >= 1
    assert len(w.worksheet.appended) == 1


# ── G. different URL remains writable (mixed batch) ───────────────────────

def test_g_mixed_batch_writes_only_new_url():
    w = two_tab_writer(main_urls=[FO_URL])
    m_old, g_old = eligible_pair(FO_URL)
    m_new, g_new = eligible_pair(OTHER_URL)
    r = w.write_production([(m_old, g_old), (m_new, g_new)])
    assert r.outcome == "SUCCESS" and r.written == 1
    assert r.duplicates == 1
    assert [row[7] for row in w.worksheet.appended] == [OTHER_URL]


# ── H. ambiguous sheet read stays fail-closed ─────────────────────────────

def test_h_unreadable_sheet_aborts_write():
    w = two_tab_writer(fail_main_read=RuntimeError("boom"),
                       fail_data_read=RuntimeError("boom"))
    m, g = eligible_pair(FO_URL)
    r = w.write_production([(m, g)])
    assert r.written == 0 and r.success is False
    assert r.outcome == "READ_FAILED", r
    assert w.worksheet.appended == [] and w.data_worksheet.appended == []


def test_h_dry_run_never_touches_sheet_read():
    w = two_tab_writer(fail_main_read=RuntimeError("must not be called"),
                       fail_data_read=RuntimeError("must not be called"))
    w.dry_run = True
    m, g = eligible_pair(FO_URL)
    r = w.write_production([(m, g)])
    assert r.outcome == "DRY_RUN" and r.written == 0


# ── I. WriteResult semantics stay truthful ────────────────────────────────

def test_i_result_counts_only_new_rows():
    w = two_tab_writer(main_urls=[FO_URL])
    m_old, g_old = eligible_pair(FO_URL)
    m_new, g_new = eligible_pair(OTHER_URL)
    r = w.write_production([(m_old, g_old), (m_new, g_new)])
    assert r.attempted == 2 and r.written == 1 and r.failed == 0
    assert r.duplicates == 1
    assert r.destinations == {"LinkedIn Hiring Leads": 1}
    assert "duplicate" in r.error.lower()


def test_i_all_duplicate_is_not_reported_as_success():
    w = two_tab_writer(main_urls=[FO_URL])
    m, g = eligible_pair(FO_URL)
    r = w.write_production([(m, g)])
    assert r.outcome != "SUCCESS", "a pure no-op must not claim SUCCESS"
    assert r.outcome == "DUPLICATE" and r.written == 0


# ── J. historical rows untouched ──────────────────────────────────────────

def test_j_existing_history_preserved_and_only_new_appended():
    history_main = ["https://old/main-1", "https://old/main-2"]
    history_data = ["https://old/data-1"]
    w = two_tab_writer(main_urls=history_main, data_urls=history_data)
    m, g = eligible_pair(OTHER_URL)
    r = w.write_production([(m, g)])
    assert r.outcome == "SUCCESS" and r.written == 1
    assert w.worksheet._urls == history_main  # history intact
    assert w.data_worksheet._urls == history_data
    assert len(w.worksheet.appended) == 1  # only the new row appended
