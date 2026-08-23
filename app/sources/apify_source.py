import json
import urllib.parse
from typing import List
from apify_client import ApifyClient
from app.sources.base import ScraperSource
from app.models import RawPost

class ApifySource(ScraperSource):
    def __init__(self, api_token: str):
        self.client = ApifyClient(api_token)
        self.actor_id = "supreme_coder/linkedin-post"
        # Populated per search_posts() call with any in-band actor error records.
        self.last_errors: List[str] = []
        
    def search_posts(self, query: str, limit: int) -> List[RawPost]:
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://www.linkedin.com/search/results/content/?keywords={encoded_query}&sortBy=%22date_posted%22"
        
        run_input = {
            "urls": [search_url],
            "limitPerSource": limit,
            "deepScrape": False
        }
        
        run = self.client.actor(self.actor_id).call(run_input=run_input)
        if not run:
            raise Exception("Actor run failed to start or complete.")

        posts = []
        self.last_errors = []
        for item in self.client.dataset(run.default_dataset_id).iterate_items():
            # The actor reports failures in-band, as ordinary dataset records
            # ({"error": "No posts found", "inputUrl": ...}), while the run itself
            # still finishes SUCCEEDED. These used to be converted into RawPosts
            # with every field blank, so the run summary claimed posts had been
            # scraped when nothing had been.
            if item.get("error"):
                self.last_errors.append(str(item.get("error")))
                continue

            post_url = item.get("url") or item.get("urn") or ""
            if not post_url:
                self.last_errors.append("dataset record without a post URL")
                continue

            author = item.get("author") or {}
            author_name = item.get("authorName") or author.get("name", "")
            author_profile_url = item.get("authorProfileUrl") or author.get("profileUrl", "")

            posts.append(RawPost(
                post_url=post_url,
                post_date=item.get("postedAtISO") or "",
                text=item.get("text") or "",
                author_name=author_name,
                author_profile_url=author_profile_url,
                company="Unclear",
            ))

        if self.last_errors and not posts:
            print(f"  Actor returned no usable posts: {self.last_errors[0]}")

        return posts
