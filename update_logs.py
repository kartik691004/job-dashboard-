import pandas as pd
import datetime

excel_file = "excel/Indian_Startup_Outreach.xlsx"

# Append log for the latest run
log_row = {
    "Date": "2026-07-31",
    "Companies Found": 6,
    "Companies Added": 6,
    "Contacts Found": 12,
    "Errors": 0,
    "Duration": "20248.4s",
    "Status": "SUCCESS"
}

df = pd.read_excel(excel_file, sheet_name="Logs")
df = pd.concat([df, pd.DataFrame([log_row])], ignore_index=True)

with pd.ExcelWriter(excel_file, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
    df.to_excel(writer, sheet_name="Logs", index=False)

print("Logs sheet updated successfully.")
