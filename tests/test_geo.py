"""
test_geo.py — India geography filter.

Every trap here is real, not hypothetical. Two were found by measuring candidate terms
against a live 330-post corpus and would have shipped as bugs otherwise:

  * "punjab" matched twice, both times on "Lahore, Punjab, Pakistan".
  * ".in" domain matching matched 80 times, every one on "lnkd.in".

The rest (Indiana, Indian Ocean, Hyderabad/Pakistan, Delhi/Ontario) are name collisions
that a naive substring filter gets wrong.
"""
import pytest

from app.geo import is_india


# ── Substring traps on "india" ─────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "I work in Indiana",
    "Hiring in Indianapolis, IN",
    "relocating to Indiana University",
    "sailing across the Indian Ocean",
    "British Indian Ocean Territory",
    "the West Indies cricket team",
    "American Indian Heritage Month",
    "Indian Wells tennis tournament",
    "Indian River County, Florida",
])
def test_india_substring_traps_are_not_india(text):
    ok, _ = is_india(text, "")
    assert not ok, f"wrongly accepted {text!r}"


def test_linkedin_shortener_is_not_an_india_signal():
    """lnkd.in is LinkedIn's URL shortener and appears in ~a quarter of all posts.
    Matching ".in" as an Indian domain fired on 80/330 records in the real corpus."""
    ok, _ = is_india("Apply here https://lnkd.in/eruUdaEu", "")
    assert not ok


# ── Place-name collisions with other countries ─────────────────────────────────

@pytest.mark.parametrize("location", [
    "Lahore, Punjab, Pakistan (On-site)",
    "Hyderabad, Sindh, Pakistan",
    "Delhi, Ontario, Canada",
    "Kochi, Japan",
    "Salem, Oregon, United States",
    "United States (Remote)",
    "Philippines (Remote)",
    "London Area, United Kingdom (Hybrid)",
    "New York City Metropolitan Area (Hybrid)",
    "Latin America (Remote)",
])
def test_foreign_job_locations_are_rejected(location):
    ok, _ = is_india("", location)
    assert not ok, f"wrongly accepted {location!r}"


def test_punjab_alone_is_never_india():
    """Punjab spans the India/Pakistan border; it is not usable as an India term."""
    ok, _ = is_india("role based in Punjab", "")
    assert not ok


# ── True positives ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("location", [
    "Noida, Uttar Pradesh, India (On-site)",
    "Bengaluru, Karnataka, India (On-site)",
    "India (Remote)",
    "Bangalore Urban, Karnataka, India (On-site)",
    # Real corpus values that name an Indian city but never the country:
    "Bengaluru East",
    "Greater Bengaluru Area (On-site)",
    "Greater Delhi Area (On-site)",
])
def test_indian_job_locations_are_accepted(location):
    ok, evidence = is_india("", location)
    assert ok, f"wrongly rejected {location!r}"
    assert location in evidence


@pytest.mark.parametrize("text", [
    "Salary: 40,000 per month",          # plain, relies on city below
    "Location: Hadapsar, Pune",
    "We are hiring in Gurugram",
    "Remote role, India only",
    "office in Bengaluru",
    "IIT Bombay graduates preferred",
])
def test_indian_text_signals_are_accepted(text):
    if "40,000" in text:
        pytest.skip("no geo term in this line by design")
    ok, _ = is_india(text, "")
    assert ok, f"wrongly rejected {text!r}"


@pytest.mark.parametrize("text", [
    "Salary: ₹40,000 per month",
    "CTC 10 LPA",
    "budget is 12 lakhs",
    "raised 5 crore",
    "call +91 98765 43210",
    "this is a PAN-India role",
    "Rs. 50,000 stipend",
])
def test_indian_market_money_idioms_are_decisive(text):
    """These catch Indian posts that never name a city or the country at all —
    the expensive failure mode, since every post was already paid for."""
    ok, evidence = is_india(text, "")
    assert ok, f"wrongly rejected {text!r}"
    assert "Indian-market marker" in evidence


# ── Tier-2 cities added 2026-08-23 ─────────────────────────────────────────────
# Found by replaying the 393-row FTB internships reference sheet: a "Founder's Office
# Intern" in Ranchi at an Indian company was read as non-India. Every term below has at
# least one verified India-context match in a saved corpus.

@pytest.mark.parametrize("place", [
    "Ranchi", "Dehradun", "Guwahati", "Amritsar", "Agra", "Ghaziabad",
    "Navi Mumbai", "Raipur", "Assam", "Chhattisgarh",
])
def test_tier_two_places_are_india(place):
    ok, _ = is_india(f"Founder's Office Intern based in {place}", "")
    assert ok, f"wrongly rejected {place!r}"


# ── Indian company form: the last-resort signal ───────────────────────────────
# "Pvt. Ltd." appeared in 34 corpus records, all Indian, and is the ONLY India signal on
# remote roles that name no city. It is South-Asian rather than Indian, so it is guarded.

@pytest.mark.parametrize("text", [
    "Founder's Office Intern at Flarebox Technologies Pvt. Ltd. — remote",
    "Hiring at Guardify Tech Private Limited, work from home",
    "role at Adpulse Digital Pvt Ltd",
])
def test_indian_company_form_accepted_when_no_neighbour_named(text):
    ok, evidence = is_india(text, "")
    assert ok, f"wrongly rejected {text!r}"
    assert "Indian company form" in evidence


@pytest.mark.parametrize("text", [
    "Remote role at Systems Ltd Pvt Ltd, Lahore Pakistan",
    "Hiring at Brandix Pvt Ltd, Colombo Sri Lanka",
    "Pvt. Ltd. company based in Dhaka, Bangladesh",
    "Founder's office role at a Pvt Ltd in Kathmandu, Nepal",
])
def test_company_form_is_refused_when_a_south_asian_neighbour_is_named(text):
    """The company form is shared across South Asia, so it must not outvote an explicit
    Pakistan/Bangladesh/Sri Lanka/Nepal mention the way a city name would."""
    ok, _ = is_india(text, "")
    assert not ok, f"wrongly accepted {text!r}"


def test_neighbour_guard_does_not_apply_to_the_city_path():
    """An Indian post may legitimately mention a neighbouring market. Only the ambiguous
    company-form path is guarded — a named Indian city still wins."""
    ok, evidence = is_india("Hiring in Bengaluru; we also serve clients in Pakistan", "")
    assert ok
    assert "India place name" in evidence


def test_company_form_ranks_below_a_named_city():
    ok, evidence = is_india("Acme Pvt Ltd, office in Pune", "")
    assert ok
    assert "India place name" in evidence   # not the company-form fallback


def test_gst_alone_is_not_an_india_signal():
    """Measured at 10 corpus records, all Indian, and still rejected: Australia, Canada,
    New Zealand, Singapore and Malaysia all levy a GST."""
    ok, _ = is_india("Assist with GST filings and reconciliations", "")
    assert not ok


# ── Precedence: the job card wins over the text ────────────────────────────────

def test_job_card_location_overrides_text():
    """A US job card on a post whose text mentions Bangalore is a US job. The card is
    structured LinkedIn data; the text is prose that may reference any office."""
    ok, evidence = is_india("Our Bangalore team is growing", "Austin, Texas, United States")
    assert not ok
    assert "outside India" in evidence


def test_text_is_used_when_no_job_card():
    ok, _ = is_india("Our Bangalore team is growing", "")
    assert ok


def test_blank_input_is_not_india():
    ok, evidence = is_india("", "")
    assert not ok
    assert "no India signal" in evidence
