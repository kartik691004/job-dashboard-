from typing import Dict, Any
from app.sources.datadoping_source import DataDopingSource
from app.classifier import DeterministicClassifier
from app.sheets_writer import SheetsWriter
from app.config import (
    APIFY_API_TOKEN,
    GOOGLE_SHEETS_CREDENTIALS,
    GOOGLE_SHEETS_ID,
    GOOGLE_SHEET_WORKSHEET,
    DRY_RUN,
    SEARCH_QUERIES,
)

# USD per returned post on the datadoping actor (FREE plan, $5 per cycle from the 11th).
PER_POST_USD = 0.00155

class Orchestrator:
    def __init__(self):
        # datadoping/linkedin-posts-search-scraper replaced supreme_coder/linkedin-post,
        # whose content search has returned "No posts found" for every query since
        # 2026-08-23. This actor takes keywords directly and covers all of them in ONE
        # run, so a pipeline execution costs one run instead of one-per-query.
        self.source = DataDopingSource(APIFY_API_TOKEN)
        self.classifier = DeterministicClassifier()
        self.sheets_writer = SheetsWriter(GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID, GOOGLE_SHEET_WORKSHEET, DRY_RUN)

    def run_pipeline(self, limit: int = 10) -> Dict[str, Any]:
        """
        Execute the full LinkedIn Hiring Intelligence pipeline.

        :param limit: Maximum posts requested per keyword. The actor floors this at 10.
                      Must be a positive integer (validated at the API layer).
        """
        errors = 0
        print("Starting pipeline...")

        # 1. Scrape — all SEARCH_QUERIES keywords in a single actor run
        est_posts = len(SEARCH_QUERIES) * max(limit, 10)
        print(f"Scraping {len(SEARCH_QUERIES)} keywords in one actor run "
              f"(max {max(limit, 10)}/keyword, ceiling {est_posts} posts, ~${est_posts * PER_POST_USD:.2f})")
        try:
            raw_posts = self.source.search_all(SEARCH_QUERIES, max_posts=limit)
        except Exception as e:
            print(f"Scrape error: {e}")
            return {"scraped": 0, "unique": 0, "valid": 0, "excluded": 0,
                    "current_run_duplicates": 0, "sheet_duplicates": 0,
                    "new_rows": 0, "errors": 1}
        if self.source.last_errors:
            errors += len(self.source.last_errors)
            print(f"Actor reported {len(self.source.last_errors)} unusable records; "
                  f"first: {self.source.last_errors[0]}")
        print(f"Scraped {len(raw_posts)} posts.")

        # 3. Level 1 Deduplication — current run
        unique_raw_posts = []
        seen_urls: set = set()
        for p in raw_posts:
            if p.post_url not in seen_urls:
                seen_urls.add(p.post_url)
                unique_raw_posts.append(p)

        current_run_duplicates = len(raw_posts) - len(unique_raw_posts)

        # 4. Classify
        valid_posts = []
        excluded_count = 0
        for p in unique_raw_posts:
            try:
                classified = self.classifier.classify(p)
                if classified.is_valid:
                    valid_posts.append(classified)
                else:
                    excluded_count += 1
            except Exception as e:
                import traceback
                print(f"Classifier error on {p.post_url}: {e}\n{traceback.format_exc()}")
                errors += 1
                excluded_count += 1

        # 5. Level 2 Deduplication — Google Sheet
        try:
            existing_urls = self.sheets_writer.get_existing_urls()
        except Exception as e:
            print(f"Error reading Sheet URLs: {e}")
            errors += 1
            existing_urls = set()

        new_valid_posts = []
        sheet_duplicates = 0
        for p in valid_posts:
            if p.source_link in existing_urls:
                sheet_duplicates += 1
            else:
                new_valid_posts.append(p)

        # 6. Write to Sheets (skipped when DRY_RUN=True)
        try:
            self.sheets_writer.write_posts(new_valid_posts)
        except Exception as e:
            print(f"Sheet write error: {e}")
            errors += 1

        # 7. Summary
        summary = {
            "scraped": len(raw_posts),
            "unique": len(unique_raw_posts),
            "valid": len(valid_posts),
            "excluded": excluded_count,
            "current_run_duplicates": current_run_duplicates,
            "sheet_duplicates": sheet_duplicates,
            "new_rows": len(new_valid_posts),
            "errors": errors,
        }

        print(
            f"\nRun Summary\n"
            f"-----------\n"
            f"Scraped:                  {summary['scraped']}\n"
            f"Unique:                   {summary['unique']}\n"
            f"Valid hiring signals:     {summary['valid']}\n"
            f"Excluded:                 {summary['excluded']}\n"
            f"Current-run duplicates:   {summary['current_run_duplicates']}\n"
            f"Existing Sheet duplicates:{summary['sheet_duplicates']}\n"
            f"New rows:                 {summary['new_rows']}\n"
            f"Errors:                   {summary['errors']}"
        )

        return summary
