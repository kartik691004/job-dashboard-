"""
test_phase16_extraction.py — Phase 16 lead-quality + extraction + search tests.

Deterministic only (no Apify/Groq/Sheets). Each test pins ONE Phase-16
behavior against either a synthetic post or a frozen baseline shape. The
frozen baseline (data/validation/final_pipeline/, 507 passed / 1 skipped)
must stay green; the single intentional assertion change is documented in
tests/test_classifier.py::test_cos_hiring_bangalore.
"""
import sys
from pathlib import Path

import pytest

@pytest.fixture(autouse=True)
def allow_ungated(monkeypatch):
    monkeypatch.setattr("app.config.ALLOW_UNGATED_PRODUCTION_WRITE", True, raising=False)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import extraction as E
from app.classifier import DeterministicClassifier
from app.config import SEARCH_QUERIES
from app.models import RawPost
from app.sheets_writer import EXPECTED_HEADERS

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


FO_MUMBAI = (
    "We're Hiring | Founder's Office Associate\n"
    "Acme Robotics is hiring for a Founder's Office Associate in Mumbai (Hybrid).\n"
    "0-2 years experience. Full-time. CTC 12 LPA.\n"
    "Apply now: https://forms.gle/abcXYZ123\n"
    "Send your resume to hiring@acmerobotics.in"
)

# ── 1. Google Form extraction ─────────────────────────────────────────────

def test_google_form_long_url_extracted_verbatim():
    url = "https://docs.google.com/forms/d/e/1FAIpQLSfXXXX/viewform?usp=sf_link"
    assert E.extract_google_form_url(f"Founder's Office Mumbai. Apply: {url}") == url


def test_google_form_short_url_extracted():
    assert E.extract_google_form_url("Apply: https://forms.gle/abcXYZ123") == \
        "https://forms.gle/abcXYZ123"


def test_google_form_absent_is_empty_never_fabricated():
    assert E.extract_google_form_url("I will share the form link on DM.") == ""
    assert E.extract_google_form_url("Fill the form: https://lnkd.in/g7pJXb6G") == ""


def test_google_form_classifier_field_and_not_in_description_or_keypoints():
    r = CLF.classify(make_post(FO_MUMBAI))
    assert r.is_valid
    assert r.apply_google_form == "https://forms.gle/abcXYZ123"
    assert "forms.gle" not in r.description
    assert "forms.gle" not in (r.key_points or "")


def test_llm_google_form_validated_not_relabeled():
    # A shortlink must never become a Google Form, even via the LLM fold-in.
    from app.llm.schemas import LeadVerdict, GateResult
    from app.llm.verifier import enriched_post
    cp = CLF.classify(make_post(FO_MUMBAI))
    v = LeadVerdict(decision="ACCEPT", confidence=0.95, is_current_job=True,
                    is_genuine_hiring=True, is_target_role=True,
                    major_category="Founder's Office", india_relevance="India",
                    employment_type="Full-time",
                    apply_google_form="https://lnkd.in/g7pJXb6G")
    out = enriched_post(cp, GateResult(verdict=v, decision="ACCEPT", status="New"))
    assert out.apply_google_form == "https://forms.gle/abcXYZ123"


# ── 2. Hiring manager extraction ────────────────────────────────────────────

def test_im_hiring_attributes_person_author_with_evidence():
    r = CLF.classify(make_post(
        "I'm hiring a Founder's Office Associate in Mumbai. Full-time. DM me."))
    assert r.is_valid
    assert r.hiring_manager_name == "Aditi Rao"
    assert "hiring" in r.hiring_manager_evidence


def test_company_page_author_never_hiring_manager():
    r = CLF.classify(make_post(
        "We're hiring a Chief of Staff in Pune. Full-time. Apply now.",
        author="Acme Corp", profile="https://www.linkedin.com/company/acme/posts"))
    assert r.is_valid
    assert r.hiring_manager_name == "Unclear"
    assert r.hiring_manager_evidence == ""


def test_named_cofounder_captured_with_role():
    r = CLF.classify(make_post(
        "We are hiring a Founder's Office Intern at Tracktion in Delhi. "
        "You will work directly with me and my co-founder Rishi Jain. Full-time."))
    assert r.hiring_manager_name == "Rishi Jain"
    assert "co-founder" in r.hiring_manager_evidence


def test_recruiter_signature_captured():
    text = ("WE'RE HIRING | Manager - Talent\nMumbai. Full-time.\n"
            "Pooja Rani | Manager - Talent Acquisition\n"
            "+91-7888918009 | pooja.rani@talentpull.in")
    name, title = E.extract_recruiter_signature(text)
    assert name == "Pooja Rani"
    assert "Talent Acquisition" in title


def test_recruiter_signoff_captured():
    text = ("We're hiring for Associate - Founder's Office at D5 Sports in Bengaluru. "
            "Full-time.\nVignesh Chandrasekar\nSr HR Executive\n"
            "vignesh@fatpigventures.com")
    name, title = E.extract_recruiter_signature(text)
    assert name == "Vignesh Chandrasekar"
    assert "HR Executive" in title


def test_bare_name_without_title_or_contact_not_manager():
    assert E.extract_recruiter_signature("Thanks, Rahul Sharma") == ("", "")
    assert E.extract_named_founder("Founder's Office role in Mumbai.") == ("", "")


def test_im_hiring_myself_still_rejected():
    r = CLF.classify(make_post(
        "I'm hiring myself out as a Founder's Office consultant in Mumbai."))
    assert not r.is_valid


# ── 3. Cold email (high priority) ───────────────────────────────────────────

def test_company_email_preserved_verbatim():
    email, _ = E.choose_cold_email("Apply: bjoshi@justmovieme.com and rdave@justmovieme.com")
    assert email == "bjoshi@justmovieme.com"


def test_multiple_emails_first_in_post_wins():
    r = CLF.classify(make_post(FO_MUMBAI))
    assert r.cold_email == "hiring@acmerobotics.in"


def test_personal_gmail_without_purpose_suppressed():
    email, _ = E.choose_cold_email("Contact: john.doe@gmail.com for details.")
    assert email == "Not Available"


def test_personal_gmail_with_application_purpose_preserved():
    email, _ = E.choose_cold_email(
        "Founder's Office Executive in Bangalore. Full-time. "
        "Share their resume at sandhya306ch@gmail.com")
    assert email == "sandhya306ch@gmail.com"


def test_noreply_never_contact():
    email, _ = E.choose_cold_email("Write to noreply@acme.in for alerts.")
    assert email == "Not Available"


def test_email_never_fabricated_from_domain():
    email, _ = E.choose_cold_email(
        "Founder's Office in Mumbai. Full-time. Join Acme Robotics today.")
    assert email == "Not Available"


def test_email_absent_from_description_and_keypoints():
    r = CLF.classify(make_post(FO_MUMBAI))
    assert "hiring@acmerobotics.in" not in r.description
    assert "hiring@acmerobotics.in" not in (r.key_points or "")


# ── 4/5. Full description + key points ──────────────────────────────────────

def test_description_has_no_403_cap():
    long_text = ("We're hiring a Founder's Office Associate in Mumbai. Full-time. "
                 + "You will drive strategy, operations and growth. " * 30)
    assert len(long_text) > 800
    r = CLF.classify(make_post(long_text))
    assert r.is_valid
    assert len(r.description) > 400
    assert "strategy, operations and growth" in r.description


def test_key_points_max_four_verbatim_no_contact():
    r = CLF.classify(make_post(FO_MUMBAI))
    points = [p for p in (r.key_points or "").split("\n") if p.strip()]
    assert 1 <= len(points) <= 4
    assert "hiring@acmerobotics.in" not in r.key_points
    assert "forms.gle" not in r.key_points
    for p in points:
        body = p[2:] if p.startswith("• ") else p
        assert body in FO_MUMBAI.replace("\n", " ") or body[:40] in r.description


def test_key_points_empty_without_substance():
    assert E.build_key_points("Hi. Thanks!") == ""


# ── 6. Search keyword expansion ─────────────────────────────────────────────

def test_search_queries_expanded_and_deduped():
    # Phase 25.5 intentional update: the 28 Phase-16 + 10 Phase-17 queries are
    # preserved byte-identical in order; 10 high-signal hirer-voice queries
    # are APPENDED (48 total). The required-15 + base-precision assertions
    # below are unchanged.
    # Phase 31 intentional update: 8 queries replaced IN PLACE (ad-magnet
    # q30/q31/q35 + zero-yield q14/q22/q26/q38/q41; see
    # data/validation/phase31_query_precision/) — count stays 48, proven
    # families q0/q2/q33/q34/q46 untouched. Three replaced texts below were
    # superseded by employer-voice / alive-geo successors.
    assert len(SEARCH_QUERIES) == 48
    assert len(set(SEARCH_QUERIES)) == len(SEARCH_QUERIES)
    required = ['"founder associate" apply',
                '"founder\'s team" hiring', '"office of the CEO" hiring',
                '"CEO\'s office" hiring', '"founder\'s office" strategy',
                '"founder\'s office" operations', '"founder\'s office" startup',
                '"founder\'s office" growth',
                '"chief of staff" strategy', '"chief of staff" startup',
                '"chief of staff" operations', '"chief of staff" chairperson',
                # Phase 31 successors of the replaced Phase-16 queries:
                '"chief of staff" "we are hiring"',      # was "office of the founder" apply
                '"founder\'s office" "is hiring"',       # was "fellow"
                '"chief of staff" Mumbai']               # was Bangalore
    for q in required:
        assert q in SEARCH_QUERIES
    # Base precision queries preserved.
    assert '"founder\'s office" hiring' in SEARCH_QUERIES
    assert '"chief of staff" hiring' in SEARCH_QUERIES


# ── 7. False-negative recoveries (baseline report §8 shapes) ────────────────

def test_fn1_job_card_indore_authoritative():
    r = CLF.classify(make_post(
        "We're hiring a new Founder's Office Associate in Indore. Full-time. Apply now.",
        card_loc="Indore, Madhya Pradesh, India (On-site)"))
    assert r.is_valid
    assert r.india_relevance == "India"


def test_fn2_pvt_ltd_author_last_resort():
    r = CLF.classify(make_post(
        "Join Our Team! We're Hiring Chief of Staff. Apply now.",
        author="Kent Constructions Pvt. Ltd.",
        profile="https://www.linkedin.com/company/kent-constructions"))
    assert r.is_valid
    assert "Pvt" in r.india_evidence or "company form" in r.india_evidence


def test_fn3_fn4_seeking_signal_with_fulltime_unicode():
    sonia = ("Chief of Staff to Chairperson - Family Office\nLocation: Gurgaon\n"
             "CTC: Up to 70 LPA\nWe are seeking a high-calibre Chief of Staff to "
             "work closely with the Chairperson.")
    r = CLF.classify(make_post(sonia))
    assert r.is_valid
    assert r.hiring_intent_signals == "seeking"
    dhairya = ("We are currently seeking\nJob Description: Founder's Office - "
               "Strategic Operations & Growth\nEngagement: Full‑Time\n"
               "Location: Vidyavihar, West Mumbai")
    r2 = CLF.classify(make_post(dhairya))
    assert r2.is_valid
    assert r2.employment_type == "Full-time"


def test_fn5_im_hiring_not_jobseeker():
    r = CLF.classify(make_post(
        "I'm hiring again. I'm looking for a Founder's Office Associate in Mumbai "
        "who will work directly with me. Full-time."))
    assert r.is_valid
    assert r.hiring_manager_name == "Aditi Rao"


def test_fn6_known_indian_company_implies_india():
    r = CLF.classify(make_post(
        "INDmoney is hiring a Founder's Office Fellow to work on strategic "
        "initiatives. Available for a full-time commitment. Full-time."))
    assert r.is_valid
    assert "indmoney" in r.india_evidence


# ── 8. False-positive rejections (baseline report §§5-6 shapes) ─────────────

def test_fp_mass_hiring_roundup_rejected():
    # Faithful to the baseline Stripe post: the roundup carries a Founder's
    # Office entry (so it clears the internship/role gates) and must die at
    # the aggregator gate instead of reaching the LLM.
    r = CLF.classify(make_post(
        "MASS HIRING | BATCH: 2022-2028\n1. Company: Stripe\nRole: Software "
        "Engineer Intern\nLocation: Bangalore\n2. Company: SureBright\n"
        "Role: Software Engineer - Founder's Office\nLocation: Gurugram\n"
        "Follow for daily Job updates."))
    assert not r.is_valid
    assert "ggregator" in r.classification_reason


def test_fp_repeated_company_labels_rejected():
    body = "Fresh roles:\n" + "\n".join(
        f"{i}. Company: Acme{i}\nRole: Engineer\nLocation: Bangalore" for i in range(4))
    from app.classifier import _count_company_labels
    assert _count_company_labels(body.lower()) >= 3


def test_fp_manager_ceos_office_reporting_line_rejected():
    r = CLF.classify(make_post(
        "WE'RE HIRING | Manager - CEO's Office\nMumbai. 5-7 years.\n"
        "We are looking for a strategic professional to work closely with the "
        "Chief of Staff to the CEO."))
    assert not r.is_valid
    assert "context" in r.classification_reason


def test_fp_sales_role_with_fo_exposure_rejected():
    r = CLF.classify(make_post(
        "RK Advisory is hiring! We're looking for a Sales & Marketing Executive "
        "in Pune. CTC Up to 6 LPA. Hands-on exposure to Sales, Marketing and "
        "the Founder's Office."))
    assert not r.is_valid


def test_fp_ea_recruiting_rejected_but_fo_ea_hybrid_passes():
    rehana = CLF.classify(make_post(
        "Hi, We are recruiting for Executive Assistant to Director at Pune. "
        "3-6 years of experience as an Executive Assistant."))
    assert not rehana.is_valid
    hybrid = CLF.classify(make_post(
        "We're Hiring | Founder's Office Executive / Executive Assistant in "
        "Bangalore. Full-time. We're looking for an energetic Founder's Office "
        "Executive to work with the Founders."))
    assert hybrid.is_valid


def test_fp_candidate_repost_rejected():
    r = CLF.classify(make_post(
        "Manya was one of our founding residents. She's looking for a Founder's "
        "Office role. If you're hiring for a founder's office, talk to Manya."))
    assert not r.is_valid


def test_fp_talent_spotlight_rejected():
    r = CLF.classify(make_post(
        "TALENT SPOTLIGHT. Today we are spotlighting Phoebe, an exceptional "
        "Chief of Staff with VC experience. Target role: Chief of Staff. "
        "Salary: £65,000. Contact grace@candcsearch.co.uk."))
    assert not r.is_valid


def test_fp_isb_advice_rejected():
    r = CLF.classify(make_post(
        "Is ISB PGP worth it? Average domestic CTC 37 LPA. Recruiters hire ISB "
        "graduates into roles such as Engagement Managers, Chief of Staff. "
        "Consulting and Tech served as the primary hiring sectors."))
    assert not r.is_valid


def test_fp_skillpad_roundup_rejected():
    r = CLF.classify(make_post(
        "This week's new startup roles: We're hiring across: Sales | Bangalore. "
        "Open to considering folks from Founder's Office, GTM and Strategy."))
    assert not r.is_valid


def test_fp_rentok_multi_employer_rejected():
    r = CLF.classify(make_post(
        "We're hiring — multiple open roles across India. At RentOk: Founder's "
        "Office – Talent Acquisition, Gurugram. For my friends' companies: "
        "Pre-Sales Executive, Remote."))
    assert not r.is_valid
    assert "ggregator" in r.classification_reason


# ── 9/10. Company + email quality ───────────────────────────────────────────

def test_company_join_beats_at_client():
    text = ("We're Hiring: Founder's Office\nJoin Enqurious, an AI-native platform "
            "working with the Data and AI teams at Fractal in Bangalore. Full-time.")
    r = CLF.classify(make_post(text))
    assert r.company_name == "Enqurious"


def test_company_junk_re_rejected_and_multiline_fused():
    assert E.clean_company_candidate("re") == ""
    assert E.clean_company_candidate("Tracktion where we work") == "Tracktion"
    assert E.clean_company_candidate("Our Team") == ""
    assert E.clean_company_candidate("Hilary Rhoda Cosmetics and") == "Hilary Rhoda Cosmetics"
    assert E.clean_company_candidate("MovieMe\n\nMovieMe") == "MovieMe"


def test_company_is_looking_for_supported():
    r = CLF.classify(make_post(
        "Fat Pig Ventures LLP is looking for a Founder's Associate in Bhubaneswar. "
        "Full-time."))
    assert r.company_name == "Fat Pig Ventures LLP"


def test_company_generic_ai_startup_rejected_email_fragment_rejected():
    from app.enrichment.company_resolver import _clean_candidate, _is_generic_descriptor
    assert _is_generic_descriptor("a fast-growing AI startup")
    assert not _is_generic_descriptor("CodeRound AI")
    assert not _is_generic_descriptor("RagaAI Inc")
    assert _clean_candidate("sonia") == ""
    assert _clean_candidate("Enqurious") == "Enqurious"


def test_kali_remote_lpa_alone_still_excluded():
    r = CLF.classify(make_post(
        "Hiring: Founder Associate (Social Media Marketing). Remote Opportunity. "
        "Package: 3LPA. Looking for someone proactive."))
    assert not r.is_valid
    assert "Unclear India relevance" in r.classification_reason


def test_agrasubstring_no_longer_india_but_city_hashtag_counts():
    from app.classifier import _match_india_city
    assert not _match_india_city("follow us on instagram for updates", "agra")
    assert _match_india_city("hiring in mumbai #mumbaijobs", "mumbai")


# ── 11. Data model / sheets ─────────────────────────────────────────────────

def test_sheets_headers_23_col_backward_compatible():
    assert len(EXPECTED_HEADERS) == 23
    assert EXPECTED_HEADERS[:19] == [
        "Company Name", "Major Category", "Exact Role", "CTC", "Cold Email",
        "Hiring Manager Name", "Hiring Manager LinkedIn", "Source Link",
        "Description", "Confidence Score", "Location", "Type",
        "Experience Requirement", "Market", "India Relevance", "Post Date",
        "Scraped At", "Classification Reason", "Status",
    ]
    assert EXPECTED_HEADERS[7] == "Source Link"  # dedup column pinned
    assert EXPECTED_HEADERS[19:] == ["Hiring Manager Evidence", "Apply Google Form",
                                     "Apply Link", "Key Points"]


def test_sheets_row_carries_new_fields():
    from app.sheets_writer import SheetsWriter
    w = SheetsWriter.__new__(SheetsWriter)
    captured = {}

    class FakeWS:
        def append_rows(self, rows, value_input_option=None):
            captured["rows"] = rows
    w.dry_run = False
    w.worksheet = FakeWS()
    r = CLF.classify(make_post(FO_MUMBAI))
    assert r.is_valid
    w.write_posts([r])
    row = captured["rows"][0]
    assert len(row) == 23
    assert row[4] == "hiring@acmerobotics.in"
    assert row[19] != ""  # HM evidence
    assert row[20] == "https://forms.gle/abcXYZ123"
    assert row[21] == "https://forms.gle/abcXYZ123"
    assert 1 <= len(row[23 - 1].split("\n")) <= 4


def test_verdict_schema_new_fields_coerced():
    from app.llm.schemas import LeadVerdict
    v = LeadVerdict(apply_google_form="not a url", hiring_manager_evidence=123)
    assert v.apply_google_form == "not a url"  # shape-checked at fold-in, not schema
    assert v.hiring_manager_evidence == ""


def test_enrichment_carries_new_fields_and_no_tuple_evidence():
    from app.enrichment import Enricher
    cp = CLF.classify(make_post(FO_MUMBAI))
    assert cp.is_valid
    e = Enricher().enrich_from_classified(cp, None)
    assert e.apply_google_form == "https://forms.gle/abcXYZ123"
    assert e.apply_link == "https://forms.gle/abcXYZ123"
    assert 1 <= len(e.key_points.split("\n")) <= 4
    assert all(isinstance(s, str) for s in e.evidence_snippets)
