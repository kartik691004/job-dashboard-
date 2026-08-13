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
        for item in self.client.dataset(run.default_dataset_id).iterate_items():
            author_name = item.get("authorName") or item.get("author", {}).get("name", "")
            author_profile_url = item.get("authorProfileUrl") or item.get("author", {}).get("profileUrl", "")
            
            post = RawPost(
                post_url=item.get("url", item.get("urn", "")),
                post_date=item.get("postedAtISO", ""),
                text=item.get("text", ""),
                author_name=author_name,
                author_profile_url=author_profile_url,
                company="Unclear"
            )
            posts.append(post)
            
        return posts
