"""
test_bug_fixes.py — regression tests for the three Phase-8 live-validation bugs.

  BUG A — aggregator posts / generic geographic or government terms must never
          resolve to a single hiring company or an employer domain ("India is
          hiring", india.gov.in blocked, Geo terms never a company, gov domains
          never an employer domain).
  BUG B — a LinkedIn "/company/" URL is a COMPANY PAGE, not a person's profile.
          It must never be surfaced as a hiring manager's own LinkedIn; only an
          "/in/..." person profile may be.
  BUG C — aggregator / roundup / job-alert / job-board posts that list MANY
          employers must be rejected, without falsely rejecting a genuine
          single-employer vacancy that mentions multiple roles.

ALL OFFLINE: no network, no Sheets, no DRY_RUN change, no Apify.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classifier import DeterministicClassifier
from app.enrichment.contact_resolver import resolve
from app.models import RawPost

from app.contact import (
    ContactDiscoveryCache,
    ContactStatus,
    WebSearchContactDiscoveryProvider,
)
from app.contact.web import SearchResult

classifier = DeterministicClassifier()


def make_post(text: str, author_name="Tester", author_profile_url="http://profile") -> RawPost:
    return RawPost(
        post_url="http://test",
        post_date="2026-08-11",
        text=text,
        author_name=author_name,
        author_profile_url=author_profile_url,
    )


# ── BUG C: aggregator / roundup rejection ─────────────────────────────────────

def test_c_reject_hiring_alerts_aggregator():
    post = make_post("Hiring alerts: Founder's Office roles opening across India. 10 roles hiring right now.")
    result = classifier.classify(post)
    assert result.is_valid is False
    assert "aggregator" in result.classification_reason.lower()


def test_c_reject_many_companies_hiring():
    post = make_post("20 companies hiring a Chief of Staff right now in Bangalore. Job alerts every week.")
    result = classifier.classify(post)
    assert result.is_valid is False
    assert "aggregator" in result.classification_reason.lower()


def test_c_reject_observed_india_is_hiring_aggregator():
    # The exact failure observed in live validation (findjobwithai "India is hiring").
    # A role keyword is present so the post reaches the aggregator gate (it was
    # previously accepted because no aggregator detection existed at all).
    post = make_post("India is hiring. 10 India roles hiring right now. Apply by DM for a Founder's Office role.")
    result = classifier.classify(post)
    assert result.is_valid is False
    assert "aggregator" in result.classification_reason.lower()


def test_c_genuine_multi_role_single_employer_still_accepted():
    # A genuine employer hiring for several positions at ONE company is NOT an
    # aggregator and must still be accepted.
    post = make_post(
        "We're hiring a Founder's Office Associate at Acme in Bangalore. "
        "We have multiple roles open and are looking for strong candidates.",
    )
    result = classifier.classify(post)
    assert result.is_valid is True
    assert "Acme" in result.company_name


def test_c_genuine_fo_still_accepted():
    post = make_post("We're hiring a Founder's Office Associate at Acme in Mumbai. "
                     "DM me with your CV.")
    result = classifier.classify(post)
    assert result.is_valid is True
    assert result.major_category == "Founder's Office"


def test_c_genuine_cos_still_accepted():
    post = make_post("We are looking for a Chief of Staff to join our team in Delhi. "
                     "Full-time. 2-4 years experience.")
    result = classifier.classify(post)
    assert result.is_valid is True
    assert result.major_category == "Chief of Staff"


# ── BUG A: executives / geo / government terms are never a company ────────────

def test_a_aggregator_never_sets_company_to_india():
    post = make_post("India is hiring. 10 India roles hiring right now.")
    result = classifier.classify(post)
    # Rejected as an aggregator; even if it weren't, "India" must not be a company.
    assert result.is_valid is False


def test_a_geo_term_never_becomes_company():
    # The "<Company> is hiring" fallback regex matches "India is hiring"; the
    # geo guard must keep company_name "Unclear" instead of "India". A role
    # keyword is present so the post reaches company extraction.
    post = make_post("India is hiring for many roles today including a Chief of Staff position.")
    result = classifier.classify(post)
    assert result.company_name != "India"
    assert result.company_name == "Unclear"


def test_a_genuine_company_hiring_is_captured():
    post = make_post("NXP Semiconductors is hiring a Chief of Staff in Bangalore.")
    result = classifier.classify(post)
    assert result.company_name == "NXP Semiconductors"


def test_a_provider_never_resolves_gov_domain():
    # Even if a search returns india.gov.in, it must never be the employer domain.
    class FakeSearch:
        def search(self, q, n=5):
            return [SearchResult(title="India", url="https://www.india.gov.in")]
    class FakeFetch:
        def get(self, url, timeout_s=12.0):
            return "<html>Government portal</html>"
    p = WebSearchContactDiscoveryProvider(
        search_provider=FakeSearch(), fetcher=FakeFetch(), min_interval_s=0.0)
    ev = p.discover("India")
    assert ev.company_domain == "Not Available"
    assert ev.status == ContactStatus.NOT_FOUND


def test_a_provider_skips_generic_geo_placeholder_lookup():
    # A generic geopolitical term is never a resolvable hiring company; discover
    # must short-circuit with Unclear + explanatory notes and no network lookup.
    class BoomSearch:
        def search(self, q, n=5):
            raise AssertionError("must never be called for a geo placeholder")
    class BoomFetch:
        def get(self, url, timeout_s=12.0):
            raise AssertionError("must never be called for a geo placeholder")
    p = WebSearchContactDiscoveryProvider(
        search_provider=BoomSearch(), fetcher=BoomFetch(), min_interval_s=0.0)
    ev = p.discover("India")
    assert ev.status == ContactStatus.NOT_FOUND
    assert ev.company == "Unclear"
    assert any("generic geographic" in n for n in ev.notes)


def test_a_nic_gov_domain_never_matches_company():
    # The .nic.in / .gov.in block works even when the search returns such a host.
    class FakeSearch:
        def search(self, q, n=5):
            return [SearchResult(title="India", url="https://www.nic.in")]
    class FakeFetch:
        def get(self, url, timeout_s=12.0):
            return "<html>portal</html>"
    p = WebSearchContactDiscoveryProvider(
        search_provider=FakeSearch(), fetcher=FakeFetch(), min_interval_s=0.0)
    ev = p.discover("India")
    assert ev.company_domain == "Not Available"
    assert ev.status == ContactStatus.NOT_FOUND


# ── BUG B: /company/ URL is never a person's LinkedIn ─────────────────────────

def test_b_company_author_is_not_hiring_manager():
    # The exact failure observed: author is .../company/digitayal/posts (a
    # company account). It must NOT become the hiring manager anyway.
    post = make_post(
        "WE ARE HIRING a Founder's Office Associate at Digitayal in Bangalore.",
        author_name="Digitayal",
        author_profile_url="https://www.linkedin.com/company/digitayal/posts",
    )
    result = classifier.classify(post)
    assert "Digitayal" in result.company_name   # company IS still detected
    assert result.hiring_manager_name == "Unclear"     # person is NOT the author
    assert result.hiring_manager_linkedin == "Unclear"  # /company/ never emitted


def test_b_person_in_profile_is_hiring_manager():
    post = make_post(
        "I'm hiring a Chief of Staff at Acme in Bangalore. DM me.",
        author_name="Kartik R",
        author_profile_url="https://www.linkedin.com/in/kartik-r",
    )
    result = classifier.classify(post)
    assert result.hiring_manager_name == "Kartik R"
    assert result.hiring_manager_linkedin == "https://www.linkedin.com/in/kartik-r"


def test_b_resolver_drops_company_llm_url():
    # The contact resolver must never emit a /company/ URL as a person's LinkedIn,
    # even when the LLM verifier (incorrectly) passed one as the manager's.
    res = resolve(
        post_text="We are hiring at Acme.",
        llm_hiring_manager_name="Priya S",
        llm_hiring_manager_linkedin="https://www.linkedin.com/company/acme/posts",
    )
    assert res.name == "Priya S"
    assert res.linkedin_url == "Not Available"


def test_b_resolver_keeps_person_in_url():
    res = resolve(
        post_text="We are hiring at Acme.",
        llm_hiring_manager_name="Priya S",
        llm_hiring_manager_linkedin="https://www.linkedin.com/in/priya-s",
    )
    assert res.name == "Priya S"
    assert res.linkedin_url == "https://www.linkedin.com/in/priya-s"


def test_b_resolver_drops_company_provider_url():
    # A contact-discovery provider hint carrying a /company/ URL is dropped too.
    res = resolve(
        post_text="We are hiring at Acme.",
        author_name="Acme Talent",
        provider_hint_name="Acme Talent",
        provider_hint_linkedin="https://www.linkedin.com/company/acme/posts/about",
    )
    assert res.linkedin_url == "Not Available"
