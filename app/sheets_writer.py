import os
import sys
import time
import traceback
import gspread
from datetime import datetime, timezone
from typing import List, Set
from app.models import ClassifiedPost

# The 19-column txt-spec layout. Single source of truth: _ensure_headers verifies
# against it and the one-time migration tool writes it.
EXPECTED_HEADERS = [
    "Company Name",
    "Major Category",
    "Exact Role",
    "CTC",
    "Cold Email",
    "Hiring Manager Name",
    "Hiring Manager LinkedIn",
    "Source Link",
    "Description",
    "Confidence Score",
    "Location",
    "Type",
    "Experience Requirement",
    "Market",
    "India Relevance",
    "Post Date",
    "Scraped At",
    "Classification Reason",
    "Status",
]

def _safe_print(text: str) -> None:
    """Print text safely on any platform.

    A console whose encoding (e.g. cp1252) cannot represent some Unicode chars
    (non-breaking hyphens, emoji, etc.) would otherwise raise UnicodeEncodeError
    and crash the run. We re-encode for the *actual* stdout encoding with
    errors="replace" so nothing is ever lost fatally.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        enc = (getattr(sys.stdout, "encoding", None) or "utf-8")
        safe = text.encode(enc, errors="replace").decode(enc, errors="replace")
        print(safe)

def _with_retry(fn, attempts: int = 4, base_delay: float = 5.0):
    """Retry a Google API call against transient 403/5xx errors."""
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
                        self.worksheet = sheet.add_worksheet(title=self.worksheet_name, rows="1000", cols="19")
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
        expected_headers = EXPECTED_HEADERS
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
                if not self.dry_run:
                    raise ValueError("Header schema mismatch. Please update or clear the worksheet headers before a live run.")
            else:
                print("Existing headers match expected schema.")

    def get_existing_urls(self) -> Set[str]:
        if self.dry_run or not self.worksheet:
            return set()

        try:
            # Column 8 is Source Link
            urls = self.worksheet.col_values(8)
            return set(urls[1:])  # Skip header row
        except Exception as e:
            print(f"Error fetching existing URLs: {e}")
            return set()

    def write_posts(self, posts: List[ClassifiedPost]):
        if not posts:
            return

        rows = []
        for post in posts:
            rows.append([
                post.company_name,
                post.major_category,
                post.exact_role,
                post.ctc,
                post.cold_email,
                post.hiring_manager_name,
                post.hiring_manager_linkedin,
                post.source_link,
                post.description,
                post.confidence,
                post.location,
                post.employment_type,
                post.experience_requirement,
                post.market,
                post.india_relevance,
                post.post_date,
                post.scraped_at,
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
