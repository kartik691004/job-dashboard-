import logging
import os
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("GoogleSheetsExporter")

# Legacy src/ layer. The live pipeline writes via app/sheets_writer.py. This
# exporter must never touch the production spreadsheets, and it must never
# clear or create sheets without an explicit opt-in.
FORBIDDEN_SPREADSHEET_IDS = {
    "15fuzMFlSj2zVaseYlMYdfRaFkmCeUoCyrvKxV6mxLis",  # JOBDASHBOARD (production)
    "1f6b_QgkmeOAu0XFpiYyDHMgfkXFKEsrX4gl8IjWbI1k",  # FTB internships (read-only)
}

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
    LEGACY: exports collected Indian startup funding & founder intelligence into
    a Google Sheets workbook with 6 sheets. Inert in the current pipeline; kept
    for reference. Guarded so it cannot create a spreadsheet implicitly, cannot
    target the production/read-only spreadsheets, and cannot clear existing
    worksheets without LEGACY_EXPORTER_OVERWRITE=true.
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
        try:
            from app.tls_trust import ensure_google_trust
            ensure_google_trust()  # Phase 28.4: OS-store trust before Google TLS
        except ImportError:
            # App package not importable (non-root cwd): keep the pre-28.4
            # behaviour — default verification stays ON and fails closed.
            pass
        creds = Credentials.from_service_account_file(self.credentials_path, scopes=scopes)
        self._client = gspread.authorize(creds)
        return self._client

    def _get_or_create_spreadsheet(self):
        client = self._get_client()
        if not (self.spreadsheet_id or "").strip():
            raise ValueError(
                "No spreadsheet_id configured; this exporter refuses to "
                "create a spreadsheet implicitly."
            )
        if self.spreadsheet_id.strip() in FORBIDDEN_SPREADSHEET_IDS:
            raise PermissionError(
                "This legacy exporter must never write to the production "
                "(JOBDASHBOARD) or read-only (FTB internships) spreadsheets."
            )
        try:
            return client.open_by_key(self.spreadsheet_id)
        except gspread.SpreadsheetNotFound as exc:
            raise RuntimeError(
                f"Spreadsheet {self.spreadsheet_id} not found; refusing to create one."
            ) from exc

    def export_workbook(
        self,
        startups_data: List[Dict[str, Any]],
        founders_data: List[Dict[str, Any]],
        contacts_data: List[Dict[str, Any]],
        jobs_data: List[Dict[str, Any]] = None,
        run_log: Optional[Dict[str, Any]] = None,
    ) -> str:
        # ── Phase 22F QUARANTINE ──
        # LEGACY exporter. NOT the live LinkedIn Hiring Leads path (which
        # writes only via app/sheets_writer.py under DRY_RUN control).
        # Refuses to write unless ALLOW_LEGACY_SHEETS_WRITE=1 is set.
        if os.getenv("ALLOW_LEGACY_SHEETS_WRITE", "").lower() not in (
            "1", "true", "yes",
        ):
            raise PermissionError(
                "Refusing legacy export_workbook: set "
                "ALLOW_LEGACY_SHEETS_WRITE=1 to explicitly opt in. "
                "This exporter has no DRY_RUN gate and is NOT the "
                "production writer."
            )
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

        allow_overwrite = os.environ.get(
            "LEGACY_EXPORTER_OVERWRITE", ""
        ).strip().lower() in ("1", "true", "yes")

        existing_sheets = {ws.title: ws for ws in spreadsheet.worksheets()}
        for sheet_name, spec in sheets_spec.items():
            if sheet_name in existing_sheets:
                if not allow_overwrite:
                    raise RuntimeError(
                        f"Worksheet '{sheet_name}' already exists; refusing to "
                        "clear it. Set LEGACY_EXPORTER_OVERWRITE=true to allow."
                    )
                worksheet = existing_sheets[sheet_name]
                worksheet.clear()
            else:
                worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=100, cols=20)

            worksheet.append_row(spec["columns"])
            for row_data in spec["data"]:
                worksheet.append_row(row_data)

        logger.info(f"Google Sheets exported successfully: {spreadsheet.url}")
        return spreadsheet.url
