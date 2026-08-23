"""
test_dedup.py — Deduplication logic tests for LinkedIn Hiring Intelligence.

Covers:
  Level 1 — Current-run URL deduplication (in-memory set)
  Level 2 — Google Sheet URL deduplication (existing URL read)
  Dry-run  — SheetsWriter must never call append_rows when DRY_RUN=True
"""
import pytest
from unittest.mock import MagicMock, patch
from app.models import RawPost, ClassifiedPost
from app.sheets_writer import SheetsWriter


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    """SheetsWriter retries with backoff on network errors; skip the sleeps in tests."""
    monkeypatch.setattr("app.sheets_writer.time.sleep", lambda seconds: None)


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_raw_post(url: str, text: str = "We are hiring a Chief of Staff in Bangalore.") -> RawPost:
    return RawPost(
        post_url=url,
        post_date="2026-08-11",
        text=text,
        author_name="Tester",
        author_profile_url="http://linkedin.com/in/tester",
    )


def make_classified_post(url: str) -> ClassifiedPost:
    return ClassifiedPost(
        post_url=url,
        post_date="2026-08-11",
        text="We are hiring a Chief of Staff in Bangalore.",
        author_name="Tester",
        author_profile_url="http://linkedin.com/in/tester",
        company_name="Unclear",
        major_category="Chief of Staff",
        exact_role="Chief of Staff",
        ctc="Not Disclosed",
        cold_email="Not Available",
        hiring_manager_name="Unclear",
        hiring_manager_linkedin="Unclear",
        source_link=url,
        description="We are hiring a Chief of Staff in Bangalore.",
        confidence=1.0,
        location="Bangalore",
        employment_type="Full-time",
        experience_requirement="Not Specified",
        market="India",
        india_relevance="India",
        scraped_at="2026-08-11T12:00:00Z",
        classification_reason="Deterministic Match",
        status="New",
        matched_role_keywords="chief of staff",
        hiring_intent_signals="hiring",
        india_evidence="Matched India location: bangalore",
        is_valid=True,
    )


# ── Level 1: Current-run deduplication ────────────────────────────────────────

def test_level1_dedup_removes_duplicate_urls():
    """Same URL returned twice should collapse to one record."""
    posts = [
        make_raw_post("https://linkedin.com/posts/post-1"),
        make_raw_post("https://linkedin.com/posts/post-1"),  # duplicate
    ]
    seen_urls: set = set()
    unique = []
    for p in posts:
        if p.post_url not in seen_urls:
            seen_urls.add(p.post_url)
            unique.append(p)
    assert len(unique) == 1


def test_level1_dedup_keeps_all_unique_urls():
    """Three distinct URLs should all be retained."""
    posts = [
        make_raw_post("https://linkedin.com/posts/post-1"),
        make_raw_post("https://linkedin.com/posts/post-2"),
        make_raw_post("https://linkedin.com/posts/post-3"),
    ]
    seen_urls: set = set()
    unique = []
    for p in posts:
        if p.post_url not in seen_urls:
            seen_urls.add(p.post_url)
            unique.append(p)
    assert len(unique) == 3


def test_level1_dedup_same_url_multiple_queries():
    """Simulate post-X appearing from three different queries."""
    from_query_a = make_raw_post("https://linkedin.com/posts/post-x")
    from_query_b = make_raw_post("https://linkedin.com/posts/post-x")
    from_query_c = make_raw_post("https://linkedin.com/posts/post-x")
    all_posts = [from_query_a, from_query_b, from_query_c]

    seen_urls: set = set()
    unique = []
    for p in all_posts:
        if p.post_url not in seen_urls:
            seen_urls.add(p.post_url)
            unique.append(p)
    assert len(unique) == 1


# ── Level 2: Google Sheet deduplication ───────────────────────────────────────

def test_level2_dedup_skips_url_already_in_sheet():
    """A URL already present in the Sheet must not be added to new_valid_posts."""
    existing_urls = {"https://linkedin.com/posts/existing-post"}
    valid_posts = [make_classified_post("https://linkedin.com/posts/existing-post")]

    new_valid = [p for p in valid_posts if p.source_link not in existing_urls]
    assert len(new_valid) == 0


def test_level2_dedup_passes_new_url_to_write():
    """A URL not in the Sheet must be included in new_valid_posts."""
    existing_urls = {"https://linkedin.com/posts/old-post"}
    valid_posts = [make_classified_post("https://linkedin.com/posts/brand-new-post")]

    new_valid = [p for p in valid_posts if p.source_link not in existing_urls]
    assert len(new_valid) == 1
    assert new_valid[0].source_link == "https://linkedin.com/posts/brand-new-post"


def test_level2_dedup_mixed_old_and_new():
    """Only the new URL passes through; the old one is dropped."""
    existing_urls = {"https://linkedin.com/posts/old-1", "https://linkedin.com/posts/old-2"}
    valid_posts = [
        make_classified_post("https://linkedin.com/posts/old-1"),
        make_classified_post("https://linkedin.com/posts/new-1"),
        make_classified_post("https://linkedin.com/posts/old-2"),
        make_classified_post("https://linkedin.com/posts/new-2"),
    ]

    new_valid = [p for p in valid_posts if p.source_link not in existing_urls]
    assert len(new_valid) == 2
    urls = {p.source_link for p in new_valid}
    assert "https://linkedin.com/posts/new-1" in urls
    assert "https://linkedin.com/posts/new-2" in urls


# ── Dry-run safety ─────────────────────────────────────────────────────────────

def test_dry_run_does_not_call_append_rows():
    """SheetsWriter with dry_run=True must never touch the gspread worksheet."""
    writer = SheetsWriter(
        credentials_path="credentials.json",
        sheet_id="fake-sheet-id",
        worksheet_name="LinkedIn Hiring Leads",
        dry_run=True,
    )
    # Even if a worksheet were somehow set, append_rows must not be called.
    mock_ws = MagicMock()
    writer.worksheet = mock_ws

    posts = [make_classified_post("https://linkedin.com/posts/post-1")]
    writer.write_posts(posts)

    mock_ws.append_rows.assert_not_called()


def test_dry_run_returns_empty_existing_urls():
    """SheetsWriter.get_existing_urls() with dry_run=True returns empty set."""
    writer = SheetsWriter(
        credentials_path="credentials.json",
        sheet_id="fake-sheet-id",
        worksheet_name="LinkedIn Hiring Leads",
        dry_run=True,
    )
    result = writer.get_existing_urls()
    assert result == set()
