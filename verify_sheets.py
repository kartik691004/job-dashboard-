import os
import gspread
from dotenv import load_dotenv

load_dotenv()

credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "credentials.json")
sheet_id = os.getenv("SHEET_ID", "")

print("--- Google Sheets Verification ---")
print(f"Using credentials: {credentials_path}")
print(f"Using sheet ID: {sheet_id}")

try:
    client = gspread.service_account(filename=credentials_path)
    print("Google authentication: PASS")
    
    try:
        sheet = client.open_by_key(sheet_id)
        print("Spreadsheet access: PASS")
        
        try:
            worksheet = sheet.get_worksheet(0)
            print("Worksheet access: PASS")
            
            # Read existing headers
            headers = worksheet.row_values(1)
            print(f"Existing headers: {headers}")
            
            expected_headers = [
                "Post URL", "Post Date", "Scraped At", "Role Category",
                "Employment Type", "Detected Role Title", "Company", "Author Name",
                "Author Profile URL", "Matched Role Keywords", "Hiring Intent Signals",
                "Confidence", "Post Snippet", "Classification Reason", "Status"
            ]
            
            if not headers:
                print("Header schema compatible: EMPTY")
            elif headers == expected_headers:
                print("Header schema compatible: PASS")
            else:
                print("Header schema compatible: FAIL")
            
            # Read existing Post URLs
            post_urls = worksheet.col_values(1)
            if post_urls and post_urls[0] == "Post URL":
                post_urls = post_urls[1:]
                
            print(f"Existing rows: {len(post_urls)}")
            print(f"Existing Post URLs: {post_urls[:5]}")
            
        except Exception as e:
            print(f"Worksheet access: FAIL - {e}")
    except Exception as e:
        print(f"Spreadsheet access: FAIL - {e}")
except Exception as e:
    print(f"Google authentication: FAIL - {e}")
