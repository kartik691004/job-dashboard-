import os
import gspread
from google.oauth2.service_account import Credentials

# ── Phase 22F QUARANTINE ──────────────────────────────────────────────────────
# LEGACY script. NOT part of the live LinkedIn Hiring Leads pipeline (which
# writes only via app/sheets_writer.py under DRY_RUN control). It targets a
# hardcoded production spreadsheet and performs unconditional append_row calls
# with NO DRY_RUN gate. It refuses to run unless the operator explicitly opts
# in with ALLOW_LEGACY_SHEETS_WRITE=1. Do NOT invoke it for production work.
LEGACY_WRITE_OPT_IN = os.getenv("ALLOW_LEGACY_SHEETS_WRITE", "")

CREDENTIALS_PATH = "credentials.json"
SPREADSHEET_ID = "15fuzMFlSj2zVaseYlMYdfRaFkmCeUoCyrvKxV6mxLis"

def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    from app.tls_trust import ensure_google_trust
    ensure_google_trust()  # Phase 28.4: OS-store trust before Google TLS
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
    return gspread.authorize(creds)

def _legacy_write_allowed() -> bool:
    # Read at call time (not import time) so tests and operators can toggle
    # the opt-in without reimporting the module.
    return (os.getenv("ALLOW_LEGACY_SHEETS_WRITE", "") or LEGACY_WRITE_OPT_IN).lower() in (
        "1", "true", "yes",
    )


def main():
    if not _legacy_write_allowed():
        raise RuntimeError(
            "Refusing to run legacy update_gs.py: set "
            "ALLOW_LEGACY_SHEETS_WRITE=1 to explicitly opt in. "
            "This script has no DRY_RUN gate and is NOT the production writer."
        )
    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    
    # Update Logs
    logs_sheet = spreadsheet.worksheet("Logs")
    log_row = ["2026-07-31", 6, 6, 12, 0, "20248.4s", "SUCCESS"]
    logs_sheet.append_row(log_row)
    print("Appended to Google Sheets Logs.")
    
    # Update Startups (Emergent)
    startups_sheet = spreadsheet.worksheet("Startups")
    # Columns: Company Name, Website, Industry, City, Funding Stage, Funding Amount, Investors, Founding Year, Employee Count, Description, Source, Date Added
    startup_row = [
        "Emergent", "emergent.ai", "Artificial Intelligence", "Bengaluru", 
        "Late Stage", "$130 Million", "Undisclosed", "2023", "100+", 
        "AI software firm that recently raised $130M.", "Web Search (News)", "2026-07-31"
    ]
    startups_sheet.append_row(startup_row)
    print("Appended Emergent to Google Sheets Startups.")

if __name__ == "__main__":
    main()
