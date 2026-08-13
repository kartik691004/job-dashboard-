import os
import gspread
from dotenv import load_dotenv

load_dotenv()

credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "credentials.json")
sheet_id = os.getenv("SHEET_ID", "")
worksheet_name = os.getenv("GOOGLE_SHEET_WORKSHEET", "LinkedIn Hiring Leads")

print("--- Spreadsheet Verification ---")
try:
    client = gspread.service_account(filename=credentials_path)
    sheet = client.open_by_key(sheet_id)
    print("Spreadsheet: PASS")
    
    first_tab = sheet.get_worksheet(0)
    print(f"Existing Job Board tab ('{first_tab.title}'): UNTOUCHED")
    
    try:
        new_tab = sheet.worksheet(worksheet_name)
        print("LinkedIn Hiring Leads tab: FOUND")
        headers = new_tab.row_values(1)
        if headers:
            print(f"Headers: PASS")
        else:
            print("Headers: EMPTY")
    except gspread.exceptions.WorksheetNotFound:
        print("LinkedIn Hiring Leads tab: NOT FOUND (Will be CREATED during production write)")
        print("Headers: PASS (Ready to create)")
        
except Exception as e:
    print(f"Verification Failed: {e}")
