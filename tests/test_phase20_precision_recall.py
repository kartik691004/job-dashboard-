"""
test_phase20_precision_recall.py — Phase 20 precision-safe recall + LLM
reliability hardening.

Offline only (no Apify/Groq/Sheets/network). Every Phase-20 change carries a
positive regression fixture AND at least one adversarial/negative fixture.
Protected invariants pinned here: known FPs stay rejected, employment UNKNOWN
policy, HM/contact strictness, derived-input cap, UNAVAILABLE/null semantics,
429-never-REJECT, aggregator/repost protections.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import extraction as E
from app.classifier import (
    DeterministicClassifier,
    _career_opps_only_in_promo_boilerplate,
    _fold_styled_alphanumerics,
    _normalize_text,
)
from app.config import DRY_RUN
from app.llm.base import LLMError
from app.llm.recovery import (
    MAX_RECOVERY_ATTEMPTS,
    is_rate_limited_error,
    recover_record,
    recovery_availability,
    should_retry_recovery,
)
from app.llm.verifier import SYSTEM_PROMPT, Verifier
from app.models import RawPost
from app.resolution import (
    EmployerState,
    EmploymentState,
    ResolutionState,
    employer_state_of,
    hm_state_of,
    resolve_company,
    resolve_employment,
    resolve_hm,
)

CLF = DeterministicClassifier()


def make_post(text, author="Aditi Rao",
              profile="https://linkedin.com/in/aditi-rao",
              card_loc="", card_co="Unclear"):
    return RawPost(
        post_url="https://www.linkedin.com/posts/test-activity-1",
        post_date="2026-09-01",
        text=text,
        author_name=author,
        author_profile_url=profile,
        company=card_co,
        job_card_location=card_loc,
        job_card_company=card_co,
        job_card_employment_type="Unclear",
        job_card_experience="Unclear",
    )


class FakeProvider:
    """Offline stand-in: returns a fixed payload or raises a fixed error."""

    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        if self.error is not None:
            raise LLMError(self.error)
        return dict(self.payload)


# Recovery requires an explicit GroqProvider; tests fake the class name the
# same way Phase-18 tests do (production guard untouched).
FakeProvider.__name__ = "GroqProvider"


def accept_payload(**over):
    base = {"decision": "ACCEPT", "confidence": 0.95,
            "is_current_job": True, "is_genuine_hiring": True,
            "is_target_role": True, "major_category": "Data Analyst",
            "actual_role": "Data Analyst", "exact_role": "Data Analyst",
            "company": "Acme", "india_relevance": "India",
            "location": "Mumbai", "employment_type": "Full-time",
            "reason": "genuine vacancy"}
    base.update(over)
    return base


# ── 20A: styled-math alphanumeric fold ───────────────────────────────────────

# Frozen FN-1 ground truth: byte-exact post text from Phase-17 live run
# 6VKTNgMPqB2pAhv90 (raw_dataset.json, nageshwaran-subramaniyan-NJzo),
# one literal per source line, ASCII-escaped so the fixture survives any
# checkout. Verified byte-identical by construction (see check below).
SHIPROCKET_FN1_TEXT = (
    "Shiprocket is hiring for the \U0001d40f\U0001d42b\U0001d428\U0001d41d\U0001d42e\U0001d41c\U0001d42d \U0001d400\U0001d427\U0001d41a\U0001d425\U0001d432\U0001d42c\U0001d42d role\n"
    "\n"
    "\U0001d411\U0001d428\U0001d425\U0001d41e: Product Analyst\n"
    "\U0001d40b\U0001d428\U0001d41c\U0001d41a\U0001d42d\U0001d422\U0001d428\U0001d427: Gurugram\n"
    "\U0001d405\U0001d42e\U0001d427\U0001d41c\U0001d42d\U0001d422\U0001d428\U0001d427: Quick Commerce\n"
    "\U0001d40d\U0001d428.\U0001d428\U0001d41f \U0001d429\U0001d428\U0001d42c\U0001d422\U0001d42d\U0001d422\U0001d428\U0001d427\U0001d42c: 2\n"
    "\n"
    "\U0001d404\U0001d425\U0001d422\U0001d420\U0001d422\U0001d41b\U0001d422\U0001d425\U0001d422\U0001d42d\U0001d432 \U0001d402\U0001d42b\U0001d422\U0001d42d\U0001d41e\U0001d42b\U0001d422\U0001d41a:\n"
    "\u2022 Bachelor\u2019s or Master\u2019s degree in Engineering or a related field.\n"
    "\u2022 1-3 years of experience in data analytics, preferably in a \U0001d477\U0001d493\U0001d490\U0001d485\U0001d496\U0001d484\U0001d495 \U0001d48d\U0001d486\U0001d485, \U0001d484\U0001d490\U0001d48f\U0001d494\U0001d496\U0001d48e\U0001d486\U0001d493 \U0001d495\U0001d486\U0001d484\U0001d489 environment.\n"
    "\u2022 Proficiency in \U0001d47a\U0001d478\U0001d473, \U0001d477\U0001d49a\U0001d495\U0001d489\U0001d490\U0001d48f, \U0001d482\U0001d48f\U0001d485 \U0001d468\U0001d485\U0001d497\U0001d482\U0001d48f\U0001d484\U0001d486\U0001d485 \U0001d46c\U0001d499\U0001d484\U0001d486\U0001d48d is a must, along with hands on experience using \U0001d63d\U0001d644 \U0001d669\U0001d664\U0001d664\U0001d661\U0001d668 and exposure to experimentation tracking. \n"
    "\n"
    "\U0001d408\U0001d427\U0001d42d\U0001d41e\U0001d42b\U0001d41e\U0001d42c\U0001d42d\U0001d41e\U0001d41d \U0001d41c\U0001d41a\U0001d427\U0001d41d\U0001d422\U0001d41d\U0001d41a\U0001d42d\U0001d41e\U0001d42c \U0001d41c\U0001d41a\U0001d427 \U0001d41a\U0001d429\U0001d429\U0001d425\U0001d432 \U0001d428\U0001d427 \U0001d42d\U0001d421\U0001d41e \U0001d41b\U0001d41e\U0001d425\U0001d428\U0001d430 \U0001d425\U0001d422\U0001d427\U0001d424 \U0001d428\U0001d42b \U0001d41c\U0001d41a\U0001d427 \U0001d42c\U0001d421\U0001d41a\U0001d42b\U0001d41e \U0001d42d\U0001d421\U0001d41e\U0001d422\U0001d42b \U0001d402\U0001d415 \U0001d41d\U0001d422\U0001d42b\U0001d41e\U0001d41c\U0001d42d\U0001d425\U0001d432: https://lnkd.in/d9dek6tP\n"
    "\n"
    "#producthiring #analyst #productmanagement #productanalyst #businessanalyst #userexperience #analyticshiring #productfunnel #metabase #sql #python #customerexperience"
)


def test_20a_fold_maps_math_blocks_to_ascii():
    # Folded letters come out lowercase: str.lower() cannot touch these
    # caseless compatibility chars, and all matching is lowercase-normalised.
    assert _fold_styled_alphanumerics(
        "\U0001D40F\U0001D42B\U0001D428\U0001D41D\U0001D42E\U0001D41C\U0001D42D"
    ) == "product"
    # Italic smalls, sans-serif capitals, monospace smalls fold identically.
    assert _fold_styled_alphanumerics("\U0001D44E\U0001D44F\U0001D450") == "abc"
    assert _fold_styled_alphanumerics("\U0001D5A0\U0001D5A1") == "ab"
    assert _fold_styled_alphanumerics("\U0001D68A\U0001D68B") == "ab"
    # Bold digits fold.
    assert _fold_styled_alphanumerics("\U0001D7D0\U0001D7D1") == "23"


def test_20a_fold_preserves_everything_else():
    ordinary = "We're hiring a Data Analyst in Mumbai. Full-time! ₹10 LPA."
    assert _fold_styled_alphanumerics(ordinary) == ordinary
    # Fullwidth, ligatures, emoji, CJK, Greek, sub/superscripts: untouched.
    assert _fold_styled_alphanumerics("Ｄａｔａ") == "Ｄａｔａ"
    assert _fold_styled_alphanumerics("ﬁle") == "ﬁle"
    assert _fold_styled_alphanumerics("🚀📍") == "🚀📍"
    assert _fold_styled_alphanumerics("数据分析师") == "数据分析师"
    assert _fold_styled_alphanumerics("αβγ") == "αβγ"
    assert _fold_styled_alphanumerics("x² + y₂") == "x² + y₂"


def test_20a_fold_is_length_preserving():
    assert len(_fold_styled_alphanumerics(SHIPROCKET_FN1_TEXT)) == len(
        SHIPROCKET_FN1_TEXT)


def test_20a_fn1_shiprocket_is_now_candidate():
    # Mechanism (matches the stored pre-fix rejection "Data role mentioned as
    # skill/context"): the plain title "Product Analyst" matched at Stage 2,
    # but the vacancy framing "Role:" is math-bold styled and invisible to the
    # ASCII strong patterns — so the eligibility line ("experience in data
    # analytics") tripped the weak marker. The fold restores the framing.
    assert "role:" not in SHIPROCKET_FN1_TEXT.lower()
    folded = _normalize_text(SHIPROCKET_FN1_TEXT.lower())
    assert "role: product analyst" in folded
    r = CLF.classify(make_post(SHIPROCKET_FN1_TEXT))
    assert r.is_valid, r.classification_reason
    assert r.major_category == "Data Analyst"
    assert r.exact_role == "Product Analyst"  # plain-ASCII occurrence
    assert r.company_name == "Shiprocket"
    assert r.location == "Gurugram"
    assert r.experience_requirement == "1-3 years"
    assert r.employment_type == "Unclear"  # unstated: UNKNOWN policy holds
    # The apply cue itself is styled ("can apply on the below link"), so no
    # deterministic cue fires: apply_link stays "" for the LLM repair net —
    # never fabricated, never guessed.
    assert r.apply_link == ""


def test_20a_fn1_original_text_retained_for_audit():
    r = CLF.classify(make_post(SHIPROCKET_FN1_TEXT))
    assert "\U0001D40F" in r.description  # styled chars kept, not destroyed


def test_20a_styled_nontarget_with_fo_context_still_rejected():
    # Styled "Marketing Manager" folds to plain text but the context gate
    # still fires on the folded form — no leak, no new match class.
    assert _fold_styled_alphanumerics(
        "\U0001D40C\U0001D41A\U0001D42B\U0001D424\U0001D41E\U0001D42D"
        "\U0001D422\U0001D427\U0001D420") == "marketing"
    r = CLF.classify(make_post(
        "Hiring "
        "\U0001D40C\U0001D41A\U0001D42B\U0001D424\U0001D41E\U0001D42D"
        "\U0001D422\U0001D427\U0001D420 "
        "\U0001D40C\U0001D41A\U0001D427\U0001D41A\U0001D420\U0001D41E\U0001D42B"
        " for our team. The Founder's Office "
        "mindset is valued. Bangalore. Full-time. Apply now."))
    assert not r.is_valid
    assert "context" in r.classification_reason


def test_20a_styled_data_entry_still_rejected():
    # Styled "data entry" folds, so the vacancy-shape guard still catches a
    # data-entry job hiding behind styled text + a data title.
    assert _fold_styled_alphanumerics(
        "\U0001D41D\U0001D41A\U0001D42D\U0001D41A") == "data"
    r = CLF.classify(make_post(
        "Hiring Reporting Analyst for "
        "\U0001D41D\U0001D41A\U0001D42D\U0001D41A "
        "\U0001D41E\U0001D427\U0001D42D\U0001D42B\U0001D432 "
        "operations in Delhi. Full-time. Apply now."))
    assert not r.is_valid
    assert "ata-entry" in r.classification_reason


def test_20a_styled_london_still_geo_rejected():
    r = CLF.classify(make_post(
        "Hiring Data Analyst in "
        "\U0001D40B\U0001D428\U0001D427\U0001D41D\U0001D428\U0001D427. "
        "Full-time. Apply now."))
    assert not r.is_valid
    assert "Not India" in r.classification_reason


def test_20a_fullwidth_titles_do_not_match():
    r = CLF.classify(make_post(
        "Ｗｅ ａｒｅ ｈｉｒｉｎｇ ａ Ｄａｔａ Ａｎａｌｙｓｔ ｉｎ Ｍｕｍｂａｉ．"))
    assert not r.is_valid  # fold boundary documented: math block only


# ── 20B: career-opportunities promo-boilerplate exemption ────────────────────

# Frozen FN-2 ground truth: byte-exact post text from Phase-17 live run
# 6VKTNgMPqB2pAhv90 (raw_dataset.json, prashant-biradar-MrMr). The
# jobs-channel promo footer carries the exempted phrase.
SWIGGY_FN2_TEXT = (
    "\U0001f6a8 Data Science Hiring Alert | India\n"
    "\n"
    "Looking to build your career in Data Science, Machine Learning, and AI? An exciting opportunity is open for early-career professionals who enjoy solving complex business problems, building ML models, and taking data-driven solutions to production.\n"
    "\n"
    "\U0001f3e2 Company Hiring: Swiggy\n"
    "\U0001f4cc Role: Data Scientist\n"
    "\U0001f4bc Experience: 1\u20133 Years\n"
    "\U0001f552 Work Mode: Full-time\n"
    "\U0001f4cd Location: Bangalore, Karnataka\n"
    "\U0001f517 Application Link: https://lnkd.in/gds28ZR8\n"
    "\n"
    "\u2728 Build your expertise in Python, SQL, Spark, TensorFlow, Machine Learning, Deep Learning, Statistics, Recommendation Systems, Ads Optimization, Big Data, Generative AI, LLMs, NLP, and Agentic AI while working on impactful, large-scale data products.\n"
    "\n"
    "\U0001f517 For regular IT & Tech job updates, join our community: https://lnkd.in/dCzV99ED\n"
    "\n"
    "\U0001f4e2 Stay connected with us for the latest Data Analytics, Data Science, AI, Healthcare, and Tech hiring updates, career opportunities, and industry insights.\n"
    "\n"
    "#Hiring #Swiggy #DataScientist #DataScience #MachineLearning #DeepLearning #Python #SQL #TensorFlow #Spark #GenAI #LLM #NLP #AIJobs #TechJobs #JobsIndia"
)


def test_20b_fn2_swiggy_is_now_candidate():
    assert "career opportunities" in SWIGGY_FN2_TEXT.lower()
    r = CLF.classify(make_post(SWIGGY_FN2_TEXT))
    assert r.is_valid, r.classification_reason
    assert r.major_category == "Data Scientist"
    assert r.exact_role == "Data Scientist"
    # "Company Hiring: Swiggy" is not an explicit-statement shape, so the
    # company stays Unclear for LLM attribution (never guessed).
    assert r.company_name == "Unclear"
    assert r.location == "Bengaluru"
    assert r.employment_type == "Full-time"
    assert r.apply_link == "https://lnkd.in/gds28ZR8"


def test_20b_exemption_requires_promo_framing():
    assert _career_opps_only_in_promo_boilerplate(
        "follow x for daily hiring updates, career opportunities, and "
        "industry insights!")
    assert not _career_opps_only_in_promo_boilerplate(
        "explore career opportunities across our portfolio companies")
    assert not _career_opps_only_in_promo_boilerplate("no mention here")


def test_20b_real_roundup_with_same_footer_still_rejected():
    r = CLF.classify(make_post(
        "Founder's Office & Chief of Staff Openings in India (Last 24 "
        "Hours). 11 fresh roles: Founder's Office at Pixelo, Chief of Staff "
        "at AppVersal. Follow JobsDaily for daily hiring updates, career "
        "opportunities, and industry insights!"))
    assert not r.is_valid
    assert "ggregator" in r.classification_reason


def test_20b_nonpromo_career_mention_still_rejected():
    r = CLF.classify(make_post(
        "Explore career opportunities across our portfolio: Qure.ai is "
        "hiring for its CEO's Office in Mumbai. Flipkart is hiring for Ops."))
    assert not r.is_valid


def test_20b_career_opportunities_at_company_still_passes():
    r = CLF.classify(make_post(
        "Hiring Data Analyst at Acme in Pune. Full-time. Explore career "
        "opportunities at Acme below. Apply now."))
    assert r.is_valid, r.classification_reason


def test_20b_wireless_technical_mention_unaffected():
    r = CLF.classify(make_post(
        "Hiring Data Scientist for wireless protocols R&D in Bangalore. "
        "Full-time. Apply now."))
    assert r.is_valid, r.classification_reason
    assert r.major_category == "Data Scientist"


# ── 20C: applied-scientist boundary pins (no widening) ───────────────────────

def test_20c_senior_applied_scientist_routes_to_llm():
    # Phase 25.3 §4: bare "Applied Scientist" is evidence-conditional —
    # deterministic admits this genuine-shaped ML vacancy (supporting
    # evidence present); the LLM applies the brief's target list
    # (fail-closed). Without evidence it stays out (see 25.3 matrix).
    r = CLF.classify(make_post(
        "We are hiring a Senior Applied Scientist in Bangalore to build "
        "machine learning models. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Scientist"


def test_20c_applied_science_symposium_rejected():
    r = CLF.classify(make_post(
        "Applied Scientist Symposium 2026: talks on ML research. Speakers "
        "from Amazon. Bangalore."))
    assert not r.is_valid


def test_20c_ml_engineer_stays_out():
    r = CLF.classify(make_post(
        "Hiring ML Engineer in Bangalore. Full-time. Apply now."))
    assert not r.is_valid


def test_20c_generic_research_scientist_stays_out():
    r = CLF.classify(make_post(
        "Hiring Research Scientist in Pune. Full-time. Apply now."))
    assert not r.is_valid


# ── 20D: single-employer multi-role prompt policy ────────────────────────────

def test_20d_prompt_distinguishes_single_employer_multi_role():
    assert "SINGLE-EMPLOYER MULTI-ROLE" in SYSTEM_PROMPT
    assert "is_aggregator=false" in SYSTEM_PROMPT
    assert "multi-EMPLOYER distribution" in SYSTEM_PROMPT


def test_20d_hard_gate_still_rejects_flagged_aggregator():
    gate = Verifier(FakeProvider({
        "decision": "ACCEPT", "confidence": 0.97, "is_current_job": True,
        "is_genuine_hiring": True, "is_target_role": True,
        "major_category": "Chief of Staff", "actual_role": "Chief of Staff",
        "is_aggregator": True, "india_relevance": "India",
        "employment_type": "Full-time"})).verify(post_text="x")
    assert gate.decision == "REJECT"
    assert any("aggregator" in reason for reason in gate.reasons)


def test_20d_single_employer_multi_role_review_passes_through():
    gate = Verifier(FakeProvider({
        "decision": "REVIEW", "confidence": 0.85, "is_current_job": True,
        "is_genuine_hiring": True, "is_target_role": True,
        "major_category": "Chief of Staff",
        "actual_role": "Chief of Staff",
        "is_aggregator": False, "india_relevance": "India",
        "employment_type": "Full-time",
        "reason": "single-employer second role needs human check"})).verify(
        post_text="Karta Initiative India Foundation is hiring a Chief of "
                  "Staff alongside a Fellow-Educator in Noida.")
    assert gate.decision == "REVIEW"
    assert gate.llm_status == "REVIEW"


# ── 20E: bounded forward apply-link recovery ─────────────────────────────────

def test_20e_cue_after_link_recovered():
    url = E.extract_apply_link(
        "Position: Founder office associate https://lnkd.in/dGMHAtGC - "
        "apply here with your resume. Gurugram. Full-time.")
    assert url == "https://lnkd.in/dGMHAtGC"


def test_20e_link_then_next_line_to_apply_recovered():
    url = E.extract_apply_link(
        "Link: https://lnkd.in/dGMHAtGC\nTo apply, send your resume. "
        "Hiring Founder Office Associate in Gurugram. Full-time.")
    assert url == "https://lnkd.in/dGMHAtGC"


def test_20e_backward_precedence_kept():
    url = E.extract_apply_link(
        "Apply here: https://lnkd.in/first. Details https://lnkd.in/second "
        "- apply today.")
    assert url == "https://lnkd.in/first"


def test_20e_later_sentence_cue_does_not_capture():
    assert E.extract_apply_link(
        "See details https://company.com/careers/123. Apply now by email. "
        "Hiring Data Analyst in Pune. Full-time.") == ""


def test_20e_article_and_profile_links_never_captured():
    assert E.extract_apply_link(
        "Read our blog https://blog.example.com/post for culture. To apply, "
        "email jobs@x.com. Hiring Data Analyst in Pune. Full-time.") == ""
    assert E.extract_apply_link(
        "Connect https://www.linkedin.com/in/someone to apply. Hiring Data "
        "Analyst in Pune. Full-time.") == ""
    assert E.extract_apply_link(
        "Our fundraise story https://example.com/blog. Hiring Data Analyst "
        "in Pune. Full-time.") == ""


def test_20e_bare_follow_page_not_an_apply_cue():
    # Vocabulary deliberately NOT extended: "interested candidates" without an
    # apply-family word must not harvest a bare company link.
    assert E.extract_apply_link(
        "Interested candidates should follow our page "
        "https://company.com/jobs for updates. Hiring Data Analyst in "
        "Pune. Full-time.") == ""


def test_20e_form_precedence_unchanged():
    url = E.extract_apply_link(
        "Apply https://forms.gle/abcXYZ123 and https://lnkd.in/other. "
        "Hiring Data Analyst in Pune. Full-time.")
    assert url == "https://forms.gle/abcXYZ123"


# ── 20F: Groq reliability / recovery queue ───────────────────────────────────
# All offline (FakeProvider only — zero Groq calls, zero network).

def _rec(text, **over):
    rec = {"source_url": "https://www.linkedin.com/posts/test-1",
           "text": text, "author_name": "T",
           "author_profile_url": "https://linkedin.com/in/t",
           "job_card_location": "", "job_card_company": "Unclear",
           "job_card_employment_type": "Unclear",
           "job_card_experience": "Unclear", "post_date": "",
           "input_fidelity": "EXACT",
           "old": {"llm_status": "UNAVAILABLE", "classification": "REVIEW"}}
    rec.update(over)
    return rec


_GOOD_TEXT = ("Looking for a Data Analyst in Bangalore. Full-time. "
              "Apply now.")


def test_20f_429_stays_hold_with_null_confidence_and_ledger():
    v = Verifier(FakeProvider(error=(
        "Groq HTTP 429 rate limit exceeded after retries")))
    out = recover_record(_rec(_GOOD_TEXT), v)
    assert out["disposition"] == "HOLD"
    assert out["recovery"]["llm_status"] == "UNAVAILABLE"
    assert out["recovery"]["llm_confidence"] is None
    assert out["attempt_count"] == 1
    assert out["last_attempt_at"] != ""
    assert out["retry_eligible"] is True
    assert recovery_availability(out) == "HOLD_RATE_LIMITED"
    assert should_retry_recovery(out) is True


def test_20f_exhausted_record_leaves_queue():
    v = Verifier(FakeProvider(error="Groq HTTP 429"))
    out = recover_record(_rec(_GOOD_TEXT, prior_attempts=3), v)
    assert out["disposition"] == "HOLD"
    assert out["attempt_count"] == 4
    assert out["retry_eligible"] is False
    assert should_retry_recovery(out) is False
    assert recovery_availability(out) == "HOLD_RATE_LIMITED"


def test_20f_evaluated_outcomes_never_requeue():
    v = Verifier(FakeProvider(accept_payload(confidence=0.95)))
    out = recover_record(_rec(_GOOD_TEXT), v)
    assert out["disposition"] == "ACCEPT"
    assert should_retry_recovery(out) is False
    assert recovery_availability(out) == "EVALUATED_ACCEPT"
    v2 = Verifier(FakeProvider({"decision": "REJECT", "confidence": 0.95,
                                "is_current_job": True,
                                "is_genuine_hiring": True,
                                "is_target_role": False,
                                "major_category": "None",
                                "actual_role": "Software Engineer"}))
    out2 = recover_record(_rec(_GOOD_TEXT), v2)
    assert out2["disposition"] == "REJECT"
    assert should_retry_recovery(out2) is False
    assert recovery_availability(out2) == "EVALUATED_REJECT"


def test_20f_429_is_never_a_rejection():
    v = Verifier(FakeProvider(error="Groq HTTP 429"))
    out = recover_record(_rec(_GOOD_TEXT), v)
    assert out["disposition"] != "REJECT"
    assert out["recovery"]["llm_status"] != "REJECT"


def test_20f_no_text_hold_is_not_retryable():
    v = Verifier(FakeProvider(accept_payload()))
    out = recover_record(_rec("", prior_attempts=0), v)
    assert out["disposition"] == "HOLD"
    assert out["attempt_count"] == 0  # no verify attempted
    assert should_retry_recovery(out) is False
    assert recovery_availability(out) == "HOLD_NO_INPUT"


def test_20f_disabled_provider_hold_stays_retryable_with_null():
    # recover_record keeps its Groq-explicit guard (it must raise for a
    # non-Groq verifier); the disabled path is verified one layer down plus
    # queue semantics over a hand-built NOT_RUN prior.
    gate = Verifier(None).verify(post_text=_GOOD_TEXT)
    assert gate.decision == "REVIEW"
    assert gate.llm_status == "NOT_RUN"
    assert gate.llm_confidence is None
    prior = {"disposition": "HOLD", "disposition_reason": "LLM unavailable",
             "recovery": {"llm_status": "NOT_RUN", "llm_error": ""},
             "attempt_count": 0}
    assert should_retry_recovery(prior) is True
    assert recovery_availability(prior) == "HOLD_OTHER"


def test_20f_rate_limit_detector_pins():
    assert is_rate_limited_error("Groq HTTP 429 rate limit exceeded")
    assert is_rate_limited_error("provider error: Groq HTTP 429")
    assert not is_rate_limited_error("LLM rejected")
    assert not is_rate_limited_error("")
    assert not should_retry_recovery({})
    assert not should_retry_recovery({"disposition": "HOLD"})
    assert MAX_RECOVERY_ATTEMPTS == 3


def test_20f_derived_accept_cap_unchanged():
    v = Verifier(FakeProvider(accept_payload(confidence=0.97)))
    out = recover_record(_rec(_GOOD_TEXT, input_fidelity="EXACT"), v)
    assert out["disposition"] == "ACCEPT"
    v2 = Verifier(FakeProvider(accept_payload(confidence=0.97)))
    out2 = recover_record(
        _rec(_GOOD_TEXT, input_fidelity="DESCRIPTION_DERIVED"), v2)
    assert out2["disposition"] == "REVIEW"  # cap preserved


# ── 20G: resolution/channel-note consistency pins (no redesign) ─────────────

def test_20g_company_tri_state_shapes():
    und = resolve_company(
        "JOB HIRING: DATA SCIENTIST. Company: Not to disclose as per our "
        "agreement with client. Location: Pune.", author_name="Pagaar India")
    assert und.value == "Unclear"
    assert employer_state_of(und) == EmployerState.INTERMEDIARY_CLIENT_UNDISCLOSED
    rep = resolve_company(
        "Hiring Data Analyst at NEC Corporation India in Noida. I am not "
        "hiring, I share job to help job seekers.", author_name="Refer Group")
    assert employer_state_of(rep) == EmployerState.INTERMEDIARY_CLIENT_UNDISCLOSED
    none = resolve_company(
        "We are hiring a Founder's Office Associate. Remote. Full-time. "
        "Apply now.", author_name="Vaishnavi Modi")
    assert employer_state_of(none) == EmployerState.EXTRACTION_UNRESOLVED
    exp = resolve_company("Acme Robotics is hiring for a Data Analyst in "
                          "Mumbai. Full-time.")
    assert exp.value == "Acme Robotics"
    assert employer_state_of(exp) == EmployerState.EXPLICIT_EMPLOYER


def test_20g_employment_unknown_is_not_a_rejection_state():
    unk = resolve_employment("Unclear")
    assert unk.resolution == ResolutionState.NOT_FOUND
    assert EmploymentState.from_string("Unclear") == EmploymentState.UNKNOWN
    assert EmploymentState.from_string("Full-time") == EmploymentState.FULL_TIME
    assert EmploymentState.from_string("Contract") == EmploymentState.CONTRACT


def test_20g_hm_poster_only_never_attributes():
    hm = resolve_hm("Unclear", "", author_name="Acme Corp",
                    author_profile_url="https://linkedin.com/company/acme",
                    text="We're hiring a Data Scientist in Pune. Full-time.")
    assert hm.value == "Unclear"
    # POSTER_ONLY (hiring-side post, non-person author) is an unattributed
    # state distinct from NOT_FOUND — either way no name is ever assigned.
    assert hm_state_of(hm) == "POSTER_ONLY"


# ── Policy invariants (must never change) ────────────────────────────────────

def test_20g_employment_unknown_post_still_candidate():
    r = CLF.classify(make_post("Hiring Data Analyst in Pune. Apply now."))
    assert r.is_valid, r.classification_reason
    assert r.employment_type == "Unclear"


def test_20g_company_page_author_never_hm():
    r = CLF.classify(make_post(
        "We're hiring a Founder's Office Associate in Mumbai. Full-time. "
        "Apply now.",
        author="Acme Corp",
        profile="https://www.linkedin.com/company/acme/posts"))
    assert r.is_valid
    assert r.hiring_manager_name == "Unclear"
    assert r.hiring_manager_evidence == ""


def test_20g_dry_run_true_and_no_sheets_in_recovery():
    assert DRY_RUN is True
    import pathlib
    src = pathlib.Path("app/llm/recovery.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            assert "gspread" not in s and "sheets_writer" not in s
