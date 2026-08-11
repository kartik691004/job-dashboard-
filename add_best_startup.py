import pandas as pd
import datetime

excel_file = "excel/Indian_Startup_Outreach.xlsx"

# The data for Emergent
new_row = {
    "Startup ID": "MANUAL-001",
    "Company Name": "Emergent",
    "Company": "Emergent", # depending on the actual header
    "Website": "emergent.ai",
    "Industry": "Artificial Intelligence",
    "City": "Bengaluru",
    "Funding Stage": "Late Stage",
    "Funding Amount": "$130 Million",
    "Priority Score": "95",
    "Investors": "Undisclosed",
    "Founding Year": "2023",
    "Employee Count": "100+",
    "Description": "AI software firm that recently raised $130M.",
    "Source": "Web Search (News)",
    "Date Added": datetime.date.today().isoformat()
}

df = pd.read_excel(excel_file, sheet_name="Startups")
print("Original Columns:", df.columns.tolist())

# Map dictionary keys to actual dataframe columns
actual_row = {}
for col in df.columns:
    if col in new_row:
        actual_row[col] = new_row[col]
    elif col == "Company":
        actual_row[col] = new_row["Company Name"]
    else:
        actual_row[col] = ""

df = pd.concat([df, pd.DataFrame([actual_row])], ignore_index=True)

with pd.ExcelWriter(excel_file, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
    df.to_excel(writer, sheet_name="Startups", index=False)

print("Successfully added Emergent to Startups sheet.")
