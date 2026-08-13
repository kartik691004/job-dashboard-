import os
import time
import traceback
import gspread
from datetime import datetime, timezone
from typing import List, Set
from app.models import ClassifiedPost


def _safe_print(text: str) -> None:
    """Print text safely on any platform, replacing unencodable characters."""
    print(text.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


def _with_retry(fn, attempts: int = 4, base_delay: float = 5.0):
    """Retry a Google API call against transient 403/5xx errors.

    Returns the call result; re-raises the last error when all attempts fail.
    """
    last_error = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e
            print(f"Google Sheets attempt {i + 1}/{attempts} failed: {e}")
            if i < attempts - 1:
                time.sleep(base_delay * (i + 1))
    raise last_error


class SheetsWriter:
    def __init__(self, credentials_path: str, sheet_id: str, worksheet_name: str, dry_run: bool = True):
        self.credentials_path = credentials_path
        self.sheet_id = sheet_id
        self.worksheet_name = worksheet_name
        self.dry_run = dry_run
        self.client = None
        self.worksheet = None

        if os.path.exists(self.credentials_path):
            try:
                self.client = _with_retry(lambda: gspread.service_account(filename=self.credentials_path))
                sheet = _with_retry(lambda: self.client.open_by_key(self.sheet_id))
                try:
                    self.worksheet = sheet.worksheet(self.worksheet_name)
                    print(f"Found existing worksheet '{self.worksheet_name}'.")
                    self._ensure_headers()
                except gspread.exceptions.WorksheetNotFound:
                    if self.dry_run:
                        print(f"Worksheet '{self.worksheet_name}' not found. It will be created during production write (DRY_RUN=false).")
                        self.worksheet = None
                    else:
                        self.worksheet = sheet.add_worksheet(title=self.worksheet_name, rows="1000", cols="15")
                        print(f"Created worksheet '{self.worksheet_name}'.")
                        self._ensure_headers()
            except Exception as e:
                print(f"Failed to authenticate or access Google Sheets: {e}")
                traceback.print_exc()
                self.dry_run = True
        else:
            print("WARNING: Google Sheets credentials not found. Forcing DRY_RUN.")
            self.dry_run = True

    def _ensure_headers(self):
        expected_headers = [
            "Post URL", "Post Date", "Scraped At", "Role Category",
            "Employment Type", "Detected Role Title", "Company", "Author Name",
            "Author Profile URL", "Matched Role Keywords", "Hiring Intent Signals",
            "Confidence", "Post Snippet", "Classification Reason", "Status"
        ]
        try:
            headers = self.worksheet.row_values(1)
        except Exception as e:
            print(f"Error reading headers: {e}")
            headers = []

        if not headers:
            if self.dry_run:
                print("Sheet is empty. Headers will be created during production write (DRY_RUN=false).")
            else:
                try:
                    self.worksheet.append_row(expected_headers)
                    print("Created header row in Google Sheet.")
                except Exception as e:
                    print(f"Error creating headers: {e}")
        else:
            if headers != expected_headers:
                print(f"CRITICAL ERROR: Existing headers do not match expected schema.")
                print(f"Expected: {expected_headers}")
                print(f"Found: {headers}")
                raise ValueError("Header schema mismatch")
            else:
                print("Existing headers match expected schema.")

    def get_existing_urls(self) -> Set[str]:
        if self.dry_run or not self.worksheet:
            return set()

        try:
            # Column A is Post URL (index 1)
            urls = self.worksheet.col_values(1)
            return set(urls[1:])  # Skip header row
        except Exception as e:
            print(f"Error fetching existing URLs: {e}")
            return set()

    def write_posts(self, posts: List[ClassifiedPost]):
        if not posts:
            return

        rows = []
        scraped_at = datetime.now(timezone.utc).isoformat()

        for post in posts:
            rows.append([
                post.post_url,
                post.post_date,
                scraped_at,
                post.role_category,
                post.employment_type,
                post.detected_role_title,
                post.company,
                post.author_name,
                post.author_profile_url,
                post.matched_role_keywords,
                post.hiring_intent_signals,
                post.confidence,
                post.post_snippet,
                post.classification_reason,
                post.status,
            ])

        if self.dry_run:
            _safe_print(f"[DRY RUN] Would write {len(rows)} rows to Google Sheets.")
            for r in rows:
                _safe_print(str(r))
        else:
            try:
                self.worksheet.append_rows(rows, value_input_option="USER_ENTERED")
                print(f"Successfully wrote {len(rows)} rows to Google Sheets.")
            except Exception as e:
                print(f"Failed to write to Google Sheets: {e}")

