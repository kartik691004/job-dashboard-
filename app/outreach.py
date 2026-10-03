"""
outreach.py — cold-mail step for the LinkedIn Hiring Leads sheet.

The pipeline writes leads with Status="New". This module is the outreach half of
the plan (AI_CONTEXT.md remaining item #1): find rows that carry a usable Cold
Email, send a short application note via SMTP, and flip Status to "Contacted".
LinkedIn posts rarely contain addresses, so this usually processes zero or few
rows — that is expected, not a bug.

Safety boundaries (same as everywhere else in the repo):
- Only ever resolves the worksheet BY NAME ("LinkedIn Hiring Leads"); refuses a
  worksheet whose id is 0, i.e. the protected gid=0 Job Board tab.
- Writes are limited to the Status cell of processed rows; nothing else on the
  sheet is modified.
- DRY_RUN=true (the .env default) reads candidates but never sends and never
  writes. run() also accepts max_per_run to cap volume per invocation.
"""
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable, Dict, List, Optional

import gspread

from app.config import (
    GOOGLE_SHEET_WORKSHEET,
    GOOGLE_SHEETS_CREDENTIALS,
    GOOGLE_SHEETS_ID,
    DRY_RUN,
    OUTREACH_FROM_EMAIL,
    OUTREACH_MAX_PER_RUN,
    OUTREACH_SENDER_NAME,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
)
from app.sheets_writer import EXPECTED_HEADERS, _with_retry

# Column names as they appear in EXPECTED_HEADERS.
COL_EMAIL = "Cold Email"
COL_STATUS = "Status"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# Cells that parse as text but are not addresses. The classifier writes
# "Not Available"; humans may have typed the rest.
PLACEHOLDER_EMAILS = {"", "-", "na", "n/a", "none", "not available", "tbd"}

STATUS_NEW = "new"
STATUS_CONTACTED = "Contacted"


def is_real_email(value: str) -> bool:
    """True only when the cell holds something we would actually mail."""
    candidate = (value or "").strip()
    return bool(candidate) and candidate.lower() not in PLACEHOLDER_EMAILS \
        and bool(EMAIL_RE.match(candidate))


@dataclass
class Lead:
    row_number: int          # 1-based Google Sheets row
    company_name: str
    exact_role: str
    hiring_manager_name: str
    email: str
    source_link: str


def compose_email(lead: Lead, sender_name: str = OUTREACH_SENDER_NAME) -> tuple:
    """Return (subject, body) for a lead. Kept deliberately plain-text and honest."""
    company = lead.company_name or "your team"
    role = lead.exact_role or "Founder's Office / Chief of Staff"
    greeting = f"Hi {lead.hiring_manager_name}," if lead.hiring_manager_name else "Hi,"
    subject = f"Application for {role} at {company}"
    body = (
        f"{greeting}\n"
        "\n"
        f"I saw your post about the {role} opening at {company} and would love to "
        "be considered.\n"
        "\n"
        "I am drawn to roles where I work directly with founders across strategy "
        "and execution, and I would welcome the chance to do that at "
        f"{company}.\n"
        "\n"
        "Happy to share my resume or anything else useful — would a short call "
        "this week work?\n"
        "\n"
        "Best regards,\n"
        f"{sender_name}\n"
    )
    return subject, body


class OutreachRunner:
    def __init__(
        self,
        credentials_path: str = GOOGLE_SHEETS_CREDENTIALS,
        sheet_id: str = GOOGLE_SHEETS_ID,
        worksheet_name: str = GOOGLE_SHEET_WORKSHEET,
        dry_run: Optional[bool] = None,
        worksheet=None,
    ):
        self.credentials_path = credentials_path
        self.sheet_id = sheet_id
        self.worksheet_name = worksheet_name
        self.dry_run = DRY_RUN if dry_run is None else dry_run
        self._worksheet = worksheet  # injection point for tests

    # ── Sheet access ──────────────────────────────────────────────────────────

    def _ensure_worksheet(self):
        if self._worksheet is None:
            if not self.sheet_id:
                raise RuntimeError("No SHEET_ID configured; refusing to guess.")
            from app.tls_trust import ensure_google_trust
            ensure_google_trust()  # Phase 28.4: OS-store trust before Google TLS
            client = _with_retry(lambda: gspread.service_account(filename=self.credentials_path))
            sheet = _with_retry(lambda: client.open_by_key(self.sheet_id))
            self._worksheet = _with_retry(lambda: sheet.worksheet(self.worksheet_name))

        # Guards run even for an injected worksheet (tests) — cheap local checks.
        ws = self._worksheet
        if getattr(ws, "id", None) == 0:
            raise RuntimeError(
                "Resolved to worksheet id 0 — that is the protected gid=0 Job Board tab."
            )
        headers = ws.row_values(1)
        if headers and headers != EXPECTED_HEADERS:
            raise RuntimeError(
                "Worksheet header does not match EXPECTED_HEADERS; aborting outreach."
            )
        return ws

    def load_candidates(self) -> List[Lead]:
        """Rows with Status=New and a real Cold Email address."""
        values = _with_retry(self._ensure_worksheet().get_all_values)
        if not values:
            return []
        header = values[0]
        idx = {name: header.index(name) for name in (COL_EMAIL, COL_STATUS) if name in header}
        if COL_EMAIL not in idx or COL_STATUS not in idx:
            raise RuntimeError(f"Sheet is missing '{COL_EMAIL}' or '{COL_STATUS}' column.")

        col_source_link = header.index("Source Link") if "Source Link" in header else None
        col_company = header.index("Company Name") if "Company Name" in header else None
        col_role = header.index("Exact Role") if "Exact Role" in header else None
        col_manager = header.index("Hiring Manager Name") if "Hiring Manager Name" in header else None

        def cell(row: List[str], i: Optional[int]) -> str:
            return row[i].strip() if i is not None and i < len(row) else ""

        candidates = []
        for offset, row in enumerate(values[1:], start=2):  # sheet rows are 1-based
            if not any(c.strip() for c in row):
                continue
            if cell(row, idx[COL_STATUS]).lower() != STATUS_NEW:
                continue
            email = cell(row, idx[COL_EMAIL])
            if not is_real_email(email):
                continue
            candidates.append(Lead(
                row_number=offset,
                company_name=cell(row, col_company),
                exact_role=cell(row, col_role),
                hiring_manager_name=cell(row, col_manager),
                email=email,
                source_link=cell(row, col_source_link),
            ))
        return candidates

    # ── Sending ───────────────────────────────────────────────────────────────

    def build_sender(self) -> Callable[[Lead, str, str], None]:
        """Real SMTP sender. STARTTLS on the configured host/port."""
        if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
            raise RuntimeError(
                "SMTP_HOST / SMTP_USER / SMTP_PASSWORD are not configured; "
                "cannot send live outreach email."
            )
        from_addr = OUTREACH_FROM_EMAIL or SMTP_USER

        def send(lead: Lead, subject: str, body: str) -> None:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = from_addr
            msg["To"] = lead.email
            msg.set_content(body)
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(SMTP_USER, SMTP_PASSWORD)
                smtp.send_message(msg)

        return send

    def run(self, max_per_run: int = OUTREACH_MAX_PER_RUN,
            send_fn: Optional[Callable[[Lead, str, str], None]] = None) -> Dict:
        """
        Process up to max_per_run candidates. Returns a summary dict:
          candidates_found, attempted, sent, failed, status_updated,
          skipped_no_email, dry_run.
        """
        candidates = self.load_candidates()
        total_rows_with_status = len(candidates)
        batch = candidates[:max(0, max_per_run)]

        summary = {
            "candidates_found": total_rows_with_status,
            "attempted": len(batch),
            "sent": 0,
            "failed": 0,
            "status_updated": 0,
            "dry_run": self.dry_run,
        }

        if not batch:
            return summary

        status_col = EXPECTED_HEADERS.index(COL_STATUS) + 1  # gspread columns are 1-based

        if self.dry_run:
            subject, body = compose_email(batch[0])
            print(f"[DRY RUN] Would email {len(batch)} lead(s); first:")
            print(f"  To      : {batch[0].email}")
            print(f"  Subject : {subject}")
            preview = body.replace("\n", "\n  ")
            print(f"  Body    : {preview}")
            return summary

        if send_fn is None:
            send_fn = self.build_sender()

        ws = self._ensure_worksheet()
        for lead in batch:
            subject, body = compose_email(lead)
            try:
                send_fn(lead, subject, body)
            except Exception as e:
                summary["failed"] += 1
                print(f"Send failed for row {lead.row_number} ({lead.email}): {e}")
                continue
            summary["sent"] += 1
            try:
                _with_retry(lambda: ws.update_cell(lead.row_number, status_col, STATUS_CONTACTED))
                summary["status_updated"] += 1
            except Exception as e:
                summary["failed"] += 1
                print(f"WARNING: sent to {lead.email} but could not update Status: {e}")

        return summary
