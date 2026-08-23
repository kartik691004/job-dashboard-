"""
datadoping_source.py — LinkedIn post search via `datadoping/linkedin-posts-search-scraper`.

Replaces `supreme_coder/linkedin-post`, whose LinkedIn *content-search* code path
started returning `{"error": "No posts found"}` for every query on 2026-08-23.
That failure is LinkedIn-side, not a config bug: the identical run input worked on
2026-08-18, pinning the older 1.3.45 build fails the same way, and `rawData: true`
still returns nothing — so the actor genuinely receives zero posts. Its
profile/company code path still works, which is why the actor looks healthy.

This actor takes keywords DIRECTLY instead of a `/search/results/content/` URL, so
it has no search-URL parsing branch to break. It also accepts a keyword ARRAY, so
one run covers every role keyword — the old source needed one actor run per
chunked query (9 runs per pipeline execution).

Verified live on 2026-08-23: 30/30 records well-formed, $0.00155 per post.
"""
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from apify_client import ApifyClient

from app.models import RawPost
from app.sources.base import ScraperSource

ACTOR_ID = "datadoping/linkedin-posts-search-scraper"

# The actor enforces a floor of 10 on max_posts; sending less is silently raised.
MIN_MAX_POSTS = 10

_ACTIVITY_RE = re.compile(r"activity-(\d+)")


def _strip_query(url: str) -> str:
    """Drop tracking query strings.

    Post URLs are clean, but `author.profile_url` always carries
    `?miniProfileUrn=urn%3Ali%3Afsd_profile%3A<viewer-specific-id>`, which differs
    per scrape and would defeat any dedup keyed on the author URL.
    """
    return (url or "").split("?")[0]


def _iso_from_epoch_ms(ms: Any) -> str:
    """Build an ISO-8601 UTC timestamp from the actor's epoch-millisecond field.

    Preferred over the actor's own `posted_at.date` ("2026-08-18 12:52:18"), which
    carries no timezone marker and is therefore ambiguous.
    """
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def _job_card(item: Dict[str, Any]) -> Dict[str, Any]:
    """Return the attached LinkedIn job card, or {} when the post has none.

    `content` is present on every record but describes whatever the post attached —
    type is one of job/text/image/article/video/document/poll. Only type=="job"
    carries hiring metadata, so everything below gates on that: an article's
    `description` is prose, not a location, and must never be read as one.
    """
    content = item.get("content") or {}
    if not isinstance(content, dict) or content.get("type") != "job":
        return {}
    return content


def _company_from_job_card(item: Dict[str, Any]) -> str:
    """Pull the company from an attached LinkedIn job card, when present.

    The card's `subtitle` is shaped "Job by <Company>". This is the first real source
    for the Company column, which the old source hardcoded to "Unclear".
    """
    subtitle = (_job_card(item).get("subtitle") or "").strip()
    if subtitle.lower().startswith("job by "):
        company = subtitle[len("job by "):].strip()
        if company:
            return company
    return "Unclear"


def _location_from_job_card(item: Dict[str, Any]) -> str:
    """Pull the job location from an attached job card.

    The card's `description` is the location string, e.g.
    "Noida, Uttar Pradesh, India (On-site)". This is the authoritative geography
    signal used by app.geo, and it was previously discarded. Present on ~18% of
    posts, which is why app.geo also scans post text.
    """
    return (_job_card(item).get("description") or "").strip()


def activity_id(item_or_url: Any) -> str:
    """Stable LinkedIn activity id, the most reliable dedup key for a post.

    Prefer the actor's own `activity_id` over `full_urn`: the two disagree on
    reshared/ugcPost records.
    """
    if isinstance(item_or_url, dict):
        aid = str(item_or_url.get("activity_id") or "").strip()
        if aid:
            return aid
        item_or_url = item_or_url.get("post_url") or ""
    m = _ACTIVITY_RE.search(str(item_or_url))
    return m.group(1) if m else ""


class DataDopingSource(ScraperSource):
    def __init__(self, api_token: str, date_filter: str = "past-24h"):
        self.client = ApifyClient(api_token)
        self.actor_id = ACTOR_ID
        self.date_filter = date_filter
        # Populated per call with any in-band actor error records.
        self.last_errors: List[str] = []

    # ── Mapping ───────────────────────────────────────────────────────────────

    def _to_raw_post(self, item: Dict[str, Any]) -> RawPost | None:
        post_url = _strip_query(item.get("post_url") or "")
        text = item.get("text") or ""
        if not post_url or not text:
            return None

        author = item.get("author") or {}
        if not isinstance(author, dict):
            author = {}

        return RawPost(
            post_url=post_url,
            post_date=_iso_from_epoch_ms(item.get("timestamp") or (item.get("posted_at") or {}).get("timestamp")),
            text=text,
            author_name=author.get("name") or item.get("owner_name") or "",
            author_profile_url=_strip_query(author.get("profile_url") or ""),
            company=_company_from_job_card(item),
            job_card_location=_location_from_job_card(item),
        )

    def _collect(self, run_input: Dict[str, Any]) -> List[RawPost]:
        run = self.client.actor(self.actor_id).call(run_input=run_input)
        if not run:
            raise Exception(f"Actor {self.actor_id} failed to start or complete.")

        posts: List[RawPost] = []
        for item in self.client.dataset(run.default_dataset_id).iterate_items():
            # Defensive: this actor emitted no in-band error records in testing,
            # but the previous one did, and silently turning those into blank
            # RawPosts is exactly how 11 days of data went missing.
            if item.get("error"):
                self.last_errors.append(str(item.get("error")))
                continue
            post = self._to_raw_post(item)
            if post is None:
                self.last_errors.append("record missing post_url or text")
                continue
            posts.append(post)
        return posts

    # ── Bulk path (preferred) ─────────────────────────────────────────────────

    def search_all(self, keywords: Iterable[str], max_posts: int = MIN_MAX_POSTS,
                   date_filter: str | None = None) -> List[RawPost]:
        """Search every keyword in a SINGLE actor run.

        `max_posts` is per keyword, not per run, and the actor floors it at 10.
        """
        keyword_list = [k for k in (kw.strip() for kw in keywords) if k]
        if not keyword_list:
            return []

        self.last_errors = []
        return self._collect({
            "keywords": keyword_list,
            "max_posts": max(int(max_posts), MIN_MAX_POSTS),
            "sort_by": "date_posted",
            "date_filter": date_filter or self.date_filter,
        })

    # ── Single-keyword path (ScraperSource interface) ──────────────────────────

    def search_posts(self, query: str, limit: int) -> List[RawPost]:
        return self.search_all([query], max_posts=limit)
