import pytest
from app.classifier import DeterministicClassifier
from app.models import RawPost

classifier = DeterministicClassifier()

def make_post(text: str) -> RawPost:
    return RawPost(
        post_url="http://test",
        post_date="2026-08-11",
        text=text,
        author_name="Tester",
        author_profile_url="http://profile"
    )

def test_cos_hiring_bangalore():
    post = make_post("We're hiring a Chief of Staff in Bangalore. 2-4 years experience.")
    result = classifier.classify(post)
    assert result.is_valid == True
    assert result.major_category == "Chief of Staff"
    assert result.market == "India"
    # Phase 16: city spellings canonicalise (Bangalore -> Bengaluru, matching
    # the enricher's location canon); state tokens drop when a city matched.
    assert result.location == "Bengaluru"

def test_fo_hiring_mumbai():
    post = make_post("Looking for a Founder's Office Associate in Mumbai. DM me with your CV.")
    result = classifier.classify(post)
    assert result.is_valid == True
    assert result.major_category == "Founder's Office"
    assert result.market == "India"
    assert result.location == "Mumbai"

def test_cos_ceo_office_ctc():
    post = make_post("Chief of Staff - CEO Office, Gurgaon, 2-4 years, ₹18 LPA. We are looking for candidates.")
    result = classifier.classify(post)
    assert result.is_valid == True
    assert result.ctc == "₹18 LPA"

def test_reject_job_seeker():
    post = make_post("Open to work as Chief of Staff in Bangalore.")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "exclusion" in result.classification_reason.lower() or "job" in result.classification_reason.lower()

def test_reject_joining():
    post = make_post("Excited to join Acme Corp as their new Chief of Staff in India!")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "congratulatory" in result.classification_reason.lower()

def test_reject_informational():
    post = make_post("What does a Chief of Staff do in an Indian startup?")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "informational" in result.classification_reason.lower() or "no genuine hiring" in result.classification_reason.lower()

def test_reject_internship():
    # "Chief of Staff Intern" is a legitimate target internship when combined
    # with the CoS keyword and hiring intent (Phase 15E: internship recall fix).
    # It should pass the deterministic gate and proceed to later gates.
    post = make_post("We are hiring a Chief of Staff Intern - Bangalore")
    result = classifier.classify(post)
    assert result.is_valid is True
    assert result.major_category == "Chief of Staff"


# ── Phase-9E Bug 1: internship detection must be word-bounded ────────────────

def test_international_does_not_trigger_internship():
    # "international" contains the substring "intern" but is not an internship.
    post = make_post(
        "UNISON INTERNATIONAL CONSULTING is hiring a Chief of Staff to CEO in Ahmedabad, 50 LPA."
    )
    result = classifier.classify(post)
    assert result.is_valid is True
    assert "internship" not in result.classification_reason.lower()


def test_interested_does_not_trigger_internship():
    post = make_post(
        "We're hiring a Founder's Office Associate. DM if interested in the opening. Bangalore."
    )
    result = classifier.classify(post)
    assert result.is_valid is True
    assert "internship" not in result.classification_reason.lower()


def test_internet_does_not_trigger_internship():
    post = make_post(
        "We're hiring a Founder's Office Executive at Acme, Bangalore. "
        "Internet marketing knowledge preferred."
    )
    result = classifier.classify(post)
    assert result.is_valid is True
    assert "internship" not in result.classification_reason.lower()


@pytest.mark.parametrize("desc", [
    "Founder's Office Intern in Mumbai",
    "Founder's Office internship opportunity in Delhi",
    "hiring interns for the Founder's Office",
    "Founder's Office internships available",
    "hiring an interning founder's office associate",
])
def test_genuine_internship_still_rejects(desc):
    # Phase 15E: posts with FO/CoS + internship are no longer deterministically
    # rejected for the internship reason. They may still be rejected for other
    # quality reasons (India relevance, hiring intent, etc.) which is correct.
    post = make_post(desc)
    result = classifier.classify(post)
    # Should not be rejected specifically for "internship" detection.
    # May be rejected for other reasons (India relevance, hiring intent, etc.).
    assert "internship" not in result.classification_reason.lower()

def test_reject_fo_ambiguous():
    post = make_post("FO hiring in Bangalore")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "fo keyword found but no explicit" in result.classification_reason.lower()

def test_reject_salary_discussion():
    post = make_post("Chief of Staff salary discussion - ₹20 LPA")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "informational" in result.classification_reason.lower() or "no genuine hiring" in result.classification_reason.lower()

def test_valid_remote_india():
    post = make_post("Looking for a Chief of Staff. Remote - India. ₹20 LPA")
    result = classifier.classify(post)
    assert result.is_valid == True
    assert result.market == "Remote - India"

def test_reject_comp_alone_without_india_context():
    post = make_post("Founder's Office Associate, ₹15 LPA. Apply now.")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "unclear india relevance" in result.classification_reason.lower()
    assert "compensation" in result.classification_reason.lower()

def test_reject_lpa_alone():
    post = make_post("Chief of Staff salaries can reach ₹20 LPA.")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "informational" in result.classification_reason.lower() or "no genuine hiring" in result.classification_reason.lower()

def test_reject_intl_onsite():
    post = make_post("We're hiring a Chief of Staff — London — £80k — Hybrid")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "not india" in result.classification_reason.lower()

def test_reject_remote_worldwide():
    post = make_post("We are hiring! Founder's Office — Remote Worldwide — $90k. Apply now.")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "unclear" in result.classification_reason.lower() or "not india" in result.classification_reason.lower()

def test_reject_remote_us():
    post = make_post("Chief of Staff — Remote US — $120k. We're hiring.")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "not india" in result.classification_reason.lower() or "unclear" in result.classification_reason.lower()

def test_accept_india_global():
    post = make_post("We are hiring a Chief of Staff for our India and global operations. Based in Delhi.")
    result = classifier.classify(post)
    assert result.is_valid == True
    assert result.market == "India + Global"

def test_proximity_failure():
    # Hiring signal and role keyword are > 300 chars apart
    filler = "x" * 400
    post = make_post(f"We are hiring {filler} and also talking about chief of staff in Bangalore.")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "no genuine hiring" in result.classification_reason.lower()
