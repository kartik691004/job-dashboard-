from typing import Dict, Any, List, Tuple
from app.sources.apify_source import ApifySource
from app.classifier import DeterministicClassifier
from app.sheets_writer import SheetsWriter
from app.config import APIFY_API_TOKEN, GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID, GOOGLE_SHEET_WORKSHEET, DRY_RUN, ROLE_CATEGORIES

# LinkedIn search rejects keyword queries above ~128 characters; keep each
# query comfortably under that limit.
QUERY_CHAR_BUDGET = 100


def _build_queries(keywords_by_category: Dict[str, List[str]], budget: int = QUERY_CHAR_BUDGET) -> List[Tuple[str, str]]:
    """Chunk role keywords into short OR-queries that fit LinkedIn's search limits.

    Returns a list of (category, query) pairs; a category may span multiple
    chunks when its keyword list is long.
    """
    queries = []
    for category, keywords in keywords_by_category.items():
        group = []
        group_len = 0
        for term in keywords:
            quoted = f'"{term}"'
            separators = 4 if group else 0  # " OR "
            if group and group_len + separators + len(quoted) > budget:
                queries.append((category, " OR ".join(group)))
                group, group_len = [], 0
            group.append(quoted)
            group_len += len(quoted) + (4 if len(group) > 1 else 0)
        if group:
            queries.append((category, " OR ".join(group)))
    return queries


class Orchestrator:
    def __init__(self):
        self.source = ApifySource(APIFY_API_TOKEN)
        self.classifier = DeterministicClassifier()
        self.sheets_writer = SheetsWriter(GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID, GOOGLE_SHEET_WORKSHEET, DRY_RUN)

    def run_pipeline(self, limit: int = 10) -> Dict[str, Any]:
        """
        Execute the full LinkedIn Hiring Intelligence pipeline.

        :param limit: Maximum number of posts requested per Apify search query.
                      Must be a positive integer (validated at the API layer).
        """
        errors = 0
        print("Starting pipeline...")

        # 1. Search Queries — short chunked queries per role category so
        #    LinkedIn search accepts them. Results are merged and
        #    deduplicated at Level 1 below.
        raw_posts = []
        for category, query in _build_queries(ROLE_CATEGORIES):
            print(f"Scraping LinkedIn posts for [{category}]: {query}  (limit={limit})")
            try:
                raw_posts.extend(self.source.search_posts(query, limit=limit))
            except Exception as e:
                print(f"Scrape error for [{category}]: {e}")
                errors += 1

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
                print(f"Classifier error on {p.post_url}: {e}")
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
            if p.post_url in existing_urls:
                sheet_duplicates += 1
            else:
                new_valid_posts.append(p)

        # 6. Write to Sheets (skipped when DRY_RUN=True)
        try:
            self.sheets_writer.write_posts(new_valid_posts)
        except Exception as e:
            print(f"Sheet write error: {e}")
            errors += 1

        # 7. Summary — matches spec §14
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

