import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("GoogleSheetsExporter")

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False


def _split_investors(investors: str) -> tuple:
    if not investors or investors in ("See article", "Startup India", "Undisclosed"):
        return investors or "Undisclosed", ""
    parts = [p.strip() for p in investors.replace(";", ",").split(",") if p.strip()]
    if len(parts) <= 1:
        return parts[0] if parts else "Undisclosed", ""
    return parts[0], ", ".join(parts[1:])


def _investor_type(investors: str) -> str:
    inv = (investors or "").lower()
    if "y combinator" in inv or "yc" in inv:
        return "Accelerator"
    if any(v in inv for v in ["sequoia", "peak xv", "accel", "matrix", "lightspeed", "nexus"]):
        return "Institutional VC"
    if "angel" in inv:
        return "Angel / Syndicate"
    return "VC / Investor"


def _guess_department(role: str) -> str:
    role_lower = (role or "").lower()
    if any(k in role_lower for k in ["founder", "ceo", "co-founder", "chief"]):
        return "Leadership"
    if any(k in role_lower for k in ["hr", "people", "talent", "recruiter", "hiring"]):
        return "HR / People"
    if any(k in role_lower for k in ["engineer", "developer", "tech", "cto", "vp engineering"]):
        return "Technology"
    if any(k in role_lower for k in ["product", "design", "ux"]):
        return "Product"
    if any(k in role_lower for k in ["marketing", "growth", "sales"]):
        return "Marketing / Sales"
    if any(k in role_lower for k in ["finance", "cfo", "accounting"]):
        return "Finance"
    return "Management"


class GoogleSheetsOutreachExporter:
    """
    Exports collected Indian startup funding & founder intelligence into
    a Google Sheets workbook with 6 sheets.
    """

    def __init__(
        self,
        credentials_path: str = "credentials.json",
        spreadsheet_id: str = "",
        spreadsheet_name: str = "Indian Startup Outreach",
    ):
        if not GSPREAD_AVAILABLE:
            raise RuntimeError(
                "gspread and google-auth are required for Google Sheets export. "
                "Run: pip install gspread google-auth"
            )
        self.credentials_path = credentials_path
        self.spreadsheet_id = spreadsheet_id
        self.spreadsheet_name = spreadsheet_name
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not Path(self.credentials_path).exists():
            raise FileNotFoundError(
                f"Google Sheets credentials not found at {self.credentials_path}. "
                "Download a service account JSON from Google Cloud Console."
            )
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(self.credentials_path, scopes=scopes)
        self._client = gspread.authorize(creds)
        return self._client

    def _get_or_create_spreadsheet(self):
        client = self._get_client()
        if self.spreadsheet_id:
            try:
                return client.open_by_key(self.spreadsheet_id)
            except gspread.SpreadsheetNotFound:
                logger.warning(f"Spreadsheet {self.spreadsheet_id} not found, creating new one")
        spreadsheet = client.create(self.spreadsheet_name)
        logger.info(f"Created new spreadsheet: {spreadsheet.url}")
        return spreadsheet

    def export_workbook(
        self,
        startups_data: List[Dict[str, Any]],
        founders_data: List[Dict[str, Any]],
        contacts_data: List[Dict[str, Any]],
        jobs_data: List[Dict[str, Any]] = None,
        run_log: Optional[Dict[str, Any]] = None,
    ) -> str:
        jobs_data = jobs_data or []
        spreadsheet = self._get_or_create_spreadsheet()
        id_map = {
            (s.get("company_name") or "").lower(): s.get("startup_id", "")
            for s in startups_data
        }

        sheets_spec = {
            "Startups": {
                "columns": [
                    "Startup ID", "Company", "Industry", "Funding Stage",
                    "Funding Amount", "Investors", "City", "Website", "Source URL",
                ],
                "data": [
                    [
                        s.get("startup_id", ""),
                        s.get("company_name", ""),
                        s.get("industry", ""),
                        s.get("funding_stage", ""),
                        s.get("funding_amount", ""),
                        s.get("investors", ""),
                        s.get("city", ""),
                        s.get("website", ""),
                        s.get("source_url", ""),
                    ]
                    for s in sorted(startups_data, key=lambda x: x.get("priority_score", 0), reverse=True)
                ],
            },
            "Funding Rounds": {
                "columns": [
                    "Startup ID", "Company", "Round Name", "Amount Raised",
                    "Announced Date", "Lead Investor", "Co-Investors",
                ],
                "data": [
                    [
                        s.get("startup_id", ""),
                        s.get("company_name", ""),
                        s.get("funding_stage", ""),
                        s.get("funding_amount", ""),
                        s.get("latest_funding_date", ""),
                        "",
                        s.get("investors", ""),
                    ]
                    for s in sorted(startups_data, key=lambda x: x.get("priority_score", 0), reverse=True)
                ],
            },
            "Founders & Leadership": {
                "columns": [
                    "Startup ID", "Company", "Founder Name", "CEO", "Position",
                    "Public LinkedIn URL", "Website Profile", "Email",
                ],
                "data": [
                    [
                        id_map.get(l.get("company", "").lower(), ""),
                        l.get("company", ""),
                        l.get("founder", ""),
                        l.get("ceo", ""),
                        l.get("position", ""),
                        l.get("public_linkedin_url", ""),
                        l.get("website_profile", ""),
                        l.get("email", ""),
                    ]
                    for l in founders_data
                ],
            },
            "Key Contacts": {
                "columns": [
                    "Startup ID", "Company", "Contact Name", "Role",
                    "Department", "Public LinkedIn URL", "Email",
                ],
                "data": [
                    [
                        id_map.get(c.get("company", "").lower(), ""),
                        c.get("company", ""),
                        c.get("contact_name", ""),
                        c.get("role", ""),
                        _guess_department(c.get("role", "")),
                        c.get("public_linkedin_url", ""),
                        c.get("public_business_email", c.get("email", "")),
                    ]
                    for c in contacts_data
                ],
            },
            "LinkedIn Profiles": {
                "columns": [
                    "Startup ID", "Company", "Name", "Role", "LinkedIn URL",
                ],
                "data": [
                    [
                        id_map.get((l.get("company", "")).lower(), ""),
                        l.get("company", ""),
                        l.get("founder", l.get("contact_name", "")),
                        l.get("position", l.get("role", "")),
                        l.get("public_linkedin_url", ""),
                    ]
                    for l in founders_data + contacts_data
                    if l.get("public_linkedin_url")
                ],
            },
            "Logs": {
                "columns": [
                    "Date", "Companies Found", "Companies Added",
                    "Contacts Found", "Errors", "Duration", "Status",
                ],
                "data": [[
                    run_log.get("date", "") if run_log else "",
                    run_log.get("companies_found", 0) if run_log else 0,
                    run_log.get("companies_added", 0) if run_log else 0,
                    run_log.get("contacts_found", 0) if run_log else 0,
                    run_log.get("errors", 0) if run_log else 0,
                    run_log.get("duration", "") if run_log else "",
                    run_log.get("status", "SUCCESS") if run_log else "",
                ]],
            },
        }

        existing_sheets = {ws.title: ws for ws in spreadsheet.worksheets()}
        for sheet_name, spec in sheets_spec.items():
            if sheet_name in existing_sheets:
                worksheet = existing_sheets[sheet_name]
                worksheet.clear()
            else:
                worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=100, cols=20)

            worksheet.append_row(spec["columns"])
            for row_data in spec["data"]:
                worksheet.append_row(row_data)

        logger.info(f"Google Sheets exported successfully: {spreadsheet.url}")
        return spreadsheet.url
