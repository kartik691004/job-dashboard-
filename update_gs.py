import gspread
from google.oauth2.service_account import Credentials

CREDENTIALS_PATH = "credentials.json"
SPREADSHEET_ID = "15fuzMFlSj2zVaseYlMYdfRaFkmCeUoCyrvKxV6mxLis"

def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
    return gspread.authorize(creds)

def main():
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
