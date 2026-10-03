"""
verify_sheets.py — read-only preflight check on the Google Sheets connection.

Reads nothing but the configured LinkedIn worksheet. This script previously called
sheet.get_worksheet(0), which is the protected Job Board tab: it both violated the
read/write protection on gid=0 and reported "Header schema compatible: FAIL" against a
tab that was never supposed to match. It now resolves the worksheet by name, exactly as
the pipeline does, and takes its expected header from app.sheets_writer so the two cannot
disagree.
"""
import io
import os
import sys

import gspread
from dotenv import load_dotenv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

load_dotenv()

from app.sheets_writer import EXPECTED_HEADERS

credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "credentials.json")
sheet_id = os.getenv("SHEET_ID", "")
worksheet_name = os.getenv("GOOGLE_SHEET_WORKSHEET", "LinkedIn Hiring Leads")

print("--- Google Sheets Verification ---")
print(f"Using credentials: {credentials_path}")
print(f"Using sheet ID:    {sheet_id}")
print(f"Using worksheet:   {worksheet_name}")

try:
    from app.tls_trust import ensure_google_trust
    ensure_google_trust()  # Phase 28.4: OS-store trust before Google TLS
    client = gspread.service_account(filename=credentials_path)
    print("Google authentication: PASS")

    try:
        sheet = client.open_by_key(sheet_id)
        print(f"Spreadsheet access: PASS ({sheet.title})")

        try:
            worksheet = sheet.worksheet(worksheet_name)
            print(f"Worksheet access: PASS (gid={worksheet.id}, index={worksheet.index})")

            if worksheet.id == 0 or worksheet.index == 0:
                print("Worksheet safety: FAIL - resolved to the protected first tab; aborting")
                raise SystemExit(1)
            print("Worksheet safety: PASS (not the protected gid=0 Job Board tab)")

            headers = worksheet.row_values(1)
            print(f"Existing headers ({len(headers)}): {headers}")

            if not headers:
                print("Header schema compatible: EMPTY")
            elif headers == EXPECTED_HEADERS:
                print("Header schema compatible: PASS")
            elif headers and headers[0] == "Post URL":
                print("Header schema compatible: LEGACY - run `python tools/migrate_to_txt_schema.py`")
            else:
                print("Header schema compatible: FAIL")
                print(f"  Expected ({len(EXPECTED_HEADERS)}): {EXPECTED_HEADERS}")

            # The dedup column is "Source Link" under the 19-column schema and was
            # "Post URL" (column A) under the legacy one, so the index must come from
            # the header ACTUALLY on the sheet.
            url_name = "Source Link" if "Source Link" in headers else ("Post URL" if "Post URL" in headers else "")
            if not url_name:
                print("Dedup URL column: NOT FOUND in header")
                post_urls = []
            else:
                url_col = headers.index(url_name) + 1
                print(f"Dedup URL column: {url_col} ('{url_name}', derived from the header on the sheet)")
                post_urls = worksheet.col_values(url_col)
                if post_urls and post_urls[0] == url_name:
                    post_urls = post_urls[1:]

            print(f"Existing data rows: {len(post_urls)}")
            for u in post_urls[:3]:
                print(f"  {u}")

        except gspread.exceptions.WorksheetNotFound:
            print(f"Worksheet access: FAIL - no tab named '{worksheet_name}'")
        except Exception as e:
            print(f"Worksheet access: FAIL - {e}")
    except Exception as e:
        print(f"Spreadsheet access: FAIL - {e}")
except Exception as e:
    print(f"Google authentication: FAIL - {e}")
