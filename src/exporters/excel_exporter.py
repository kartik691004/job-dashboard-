"""
Exports pipeline data to Excel and Google Sheets.
Maintains 6 specific sheets:
1. Startups
2. Leadership
3. Business Contacts
4. Funding
5. Growth Score
6. Outreach Notes

Also provides ExcelOutreachExporter (used by real_founder_pipeline.py) which
handles the 'company' / 'startup_id' data shape produced by that pipeline.

Uses append-only logic to avoid duplicate entries in existing sheets.
"""
import os
import logging
from typing import List, Dict, Any
from datetime import datetime

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from src.config import EXCEL_OUTPUT_PATH, GOOGLE_SHEETS_ENABLED

logger = logging.getLogger("ExcelExporter")

class Exporter:
    def __init__(self, excel_path: str = None):
        self.excel_path = excel_path or EXCEL_OUTPUT_PATH
        
        self.sheets_config = {
            "Startups": [
                "Company Name", "Website", "Industry", "City", "Funding Stage", 
                "Funding Amount", "Investors", "Founding Year", "Employee Count", 
                "Description", "Source", "Date Added"
            ],
            "Leadership": [
                "Company Name", "Founder(s)", "CEO", "CTO", 
                "Team Page URL", "LinkedIn URL"
            ],
            "Business Contacts": [
                "Company Name", "Contact Name", "Role", "Email", 
                "Careers Page", "LinkedIn Profile", "Source"
            ],
            "Funding": [
                "Company Name", "Latest Round", "Amount", "Date", 
                "Investors"
            ],
            "Growth Score": [
                "Company Name", "Priority Score", "Outreach Tier", 
                "AI Summary", "Growth Signals"
            ],
            "Outreach Notes": [
                "Company Name", "Contact", "Email Subject", 
                "Initial Pitch", "Day 3 Follow-up", "Day 7 Follow-up", "Status"
            ]
        }

    def _init_workbook(self) -> openpyxl.Workbook:
        """Create a new workbook with required sheets and headers if it doesn't exist."""
        wb = None
        if os.path.exists(self.excel_path):
            try:
                wb = openpyxl.load_workbook(self.excel_path)
            except Exception as e:
                logger.error(f"Failed to load existing workbook: {e}")
                # Backup corrupt file
                os.rename(self.excel_path, f"{self.excel_path}.corrupt.bak")
                
        if wb is None:
            wb = openpyxl.Workbook()
            # Remove default 'Sheet'
            if "Sheet" in wb.sheetnames:
                del wb["Sheet"]
            
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="4F81BD")
        
        for sheet_name, headers in self.sheets_config.items():
            if sheet_name not in wb.sheetnames:
                ws = wb.create_sheet(sheet_name)
                
                # Write headers
                for col_idx, header in enumerate(headers, 1):
                    cell = ws.cell(row=1, column=col_idx, value=header)
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                    
                # Set column widths
                for i in range(1, len(headers) + 1):
                    ws.column_dimensions[get_column_letter(i)].width = 25
                
        return wb

    def _get_existing_companies(self, wb: openpyxl.Workbook, sheet_name: str) -> set:
        """Get set of lowercased company names already in the sheet to prevent duplicates."""
        if sheet_name not in wb.sheetnames:
            return set()
            
        ws = wb[sheet_name]
        existing = set()
        
        # Assume Company Name is always in Column A (index 1)
        for row in range(2, ws.max_row + 1):
            cell_val = ws.cell(row=row, column=1).value
            if cell_val:
                existing.add(str(cell_val).strip().lower())
                
        return existing

    def _append_row(self, ws, row_data: List[Any]):
        """Append a single row to a worksheet with basic formatting."""
        ws.append(row_data)
        row_idx = ws.max_row
        
        for col_idx in range(1, len(row_data) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    def export(self, startups_data: List[Dict], enrichments: Dict[str, Dict]):
        """
        Export data to the 6 configured sheets.
        enrichments is a dict mapping company_name -> enrichment_data (leadership, contacts, outreach)
        """
        if not startups_data:
            logger.info("No new startups to export.")
            return

        logger.info(f"Exporting {len(startups_data)} startups to Excel: {self.excel_path}")
        os.makedirs(os.path.dirname(self.excel_path), exist_ok=True)
        
        wb = self._init_workbook()
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # 1. Startups Sheet
        startups_ws = wb["Startups"]
        existing_startups = self._get_existing_companies(wb, "Startups")
        
        # 2. Leadership Sheet
        leadership_ws = wb["Leadership"]
        existing_leadership = self._get_existing_companies(wb, "Leadership")
        
        # 3. Business Contacts Sheet (Allow multiple contacts per company)
        contacts_ws = wb["Business Contacts"]
        
        # 4. Funding Sheet
        funding_ws = wb["Funding"]
        existing_funding = self._get_existing_companies(wb, "Funding")
        
        # 5. Growth Score Sheet
        growth_ws = wb["Growth Score"]
        existing_growth = self._get_existing_companies(wb, "Growth Score")
        
        # 6. Outreach Notes Sheet
        outreach_ws = wb["Outreach Notes"]
        existing_outreach = self._get_existing_companies(wb, "Outreach Notes")
        
        new_count = 0
        
        for idx, startup in enumerate(startups_data, 1):
            company = (startup.get("company_name") or startup.get("company") or "").strip()
            if not company:
                continue

            # Ensure startup_id is stamped (for pipelines that don't pre-generate it)
            if not startup.get("startup_id"):
                startup["startup_id"] = f"STU-IND-{idx:03d}"

            comp_lower = company.lower()
            enrich_data = enrichments.get(company, {})
            
            # --- Startups Sheet ---
            if comp_lower not in existing_startups:
                self._append_row(startups_ws, [
                    company,
                    startup.get("website", ""),
                    startup.get("industry", ""),
                    startup.get("city", ""),
                    startup.get("funding_stage", ""),
                    startup.get("funding_amount", ""),
                    startup.get("investors", ""),
                    startup.get("founding_year", ""),
                    startup.get("employee_count", ""),
                    startup.get("company_description", ""),
                    startup.get("source", ""),
                    today_str
                ])
                existing_startups.add(comp_lower)
                new_count += 1
                
            # --- Funding Sheet ---
            if comp_lower not in existing_funding:
                self._append_row(funding_ws, [
                    company,
                    startup.get("funding_stage", "Unknown"),
                    startup.get("funding_amount", "Undisclosed"),
                    startup.get("latest_funding_date", ""),
                    startup.get("investors", "")
                ])
                existing_funding.add(comp_lower)
                
            # --- Growth Score Sheet ---
            if comp_lower not in existing_growth:
                self._append_row(growth_ws, [
                    company,
                    startup.get("priority_score", 0),
                    startup.get("outreach_priority", ""),
                    startup.get("ai_summary", ""),
                    "Early Stage" if "Seed" in startup.get("funding_stage", "") or "Angel" in startup.get("funding_stage", "") else ""
                ])
                existing_growth.add(comp_lower)
                
            # Processing Enrichment Data
            if enrich_data:
                lead = enrich_data.get("leadership", {})
                conts = enrich_data.get("contacts", [])
                hiring = enrich_data.get("hiring", {})
                outreach = enrich_data.get("outreach", {})
                
                # --- Leadership Sheet ---
                if comp_lower not in existing_leadership and (lead.get("founders") or lead.get("ceo")):
                    founders_str = ", ".join(lead.get("founders", []))
                    self._append_row(leadership_ws, [
                        company,
                        founders_str,
                        lead.get("ceo", ""),
                        lead.get("cto", ""),
                        lead.get("team_page_url", ""),
                        lead.get("linkedin_url", "")
                    ])
                    existing_leadership.add(comp_lower)
                    
                # --- Business Contacts Sheet ---
                # We can have multiple contacts per company, we don't deduplicate by company name here
                for c in conts:
                    self._append_row(contacts_ws, [
                        company,
                        c.get("name", ""),
                        c.get("role", ""),
                        c.get("email", ""),
                        hiring.get("careers_page_url", ""),
                        c.get("linkedin_url", ""),
                        c.get("source", "")
                    ])
                    
                # --- Outreach Notes Sheet ---
                if comp_lower not in existing_outreach and outreach:
                    self._append_row(outreach_ws, [
                        company,
                        outreach.get("contact", ""),
                        outreach.get("email_subject", ""),
                        outreach.get("initial_pitch", ""),
                        outreach.get("day3_followup", ""),
                        outreach.get("day7_followup", ""),
                        outreach.get("email_status", "DRAFT")
                    ])
                    existing_outreach.add(comp_lower)

        # Save Workbook
        try:
            wb.save(self.excel_path)
            logger.info(f"Excel export complete. Added {new_count} new companies.")
        except PermissionError:
            logger.error(f"Permission denied to save Excel. Make sure '{self.excel_path}' is not open in another program.")
        except Exception as e:
            logger.error(f"Failed to save Excel file: {e}")
            
        if GOOGLE_SHEETS_ENABLED:
            self._export_to_google_sheets(wb)

    def _export_to_google_sheets(self, wb: openpyxl.Workbook):
        """(Optional) Sync the local Excel workbook to Google Sheets"""
        logger.info("Google Sheets export is enabled, but not fully implemented in this script.")
        # In a real implementation, you would use gspread to push the data here
        pass


# ---------------------------------------------------------------------------
# ExcelOutreachExporter
# Used by real_founder_pipeline.py — handles the data shape that pipeline
# produces: keys are 'company' (not 'company_name') and 'startup_id'.
# Writes the same 6-sheet workbook schema used by GoogleSheetsOutreachExporter.
# ---------------------------------------------------------------------------

class ExcelOutreachExporter:
    """
    Excel exporter tailored for the real_founder_pipeline data shape.

    Expected dict keys in startups_data:  startup_id, company, industry,
        funding_stage, funding_amount, investors, city, website, source_url
    Expected dict keys in founders_data:  startup_id, company, founder_name,
        ceo, position, public_linkedin_url, website_profile, email
    Expected dict keys in contacts_data:  startup_id, company, contact_name,
        role, department, public_linkedin_url, email
    """

    SHEETS = {
        "Startups": [
            "Startup ID", "Company", "Industry", "Funding Stage",
            "Funding Amount", "Investors", "City", "Website", "Source URL",
        ],
        "Funding Rounds": [
            "Startup ID", "Company", "Round Name", "Amount Raised",
            "Announced Date", "Lead Investor", "Co-Investors",
        ],
        "Founders & Leadership": [
            "Startup ID", "Company", "Founder Name", "CEO", "Position",
            "Public LinkedIn URL", "Website Profile", "Email",
        ],
        "Key Contacts": [
            "Startup ID", "Company", "Contact Name", "Role",
            "Department", "Public LinkedIn URL", "Email",
        ],
        "LinkedIn Profiles": [
            "Startup ID", "Company", "Name", "Role", "LinkedIn URL",
        ],
        "Outreach": [
            "Company", "Contact Name", "Role", "Business Email",
            "Date Added", "Initial Email Sent", "Follow-up Sent",
            "Reply Status", "Last Activity", "Campaign ID", "Subject", "Body", "Notes",
        ],
        "Logs": [
            "Date", "Companies Found", "Companies Added",
            "Contacts Found", "Errors", "Duration", "Status",
        ],
    }

    def __init__(self, output_path: str = None):
        self.output_path = output_path or EXCEL_OUTPUT_PATH
        os.makedirs(os.path.dirname(self.output_path), exist_ok=True)

    # ------------------------------------------------------------------
    def _load_or_create_wb(self) -> openpyxl.Workbook:
        if os.path.exists(self.output_path):
            try:
                wb = openpyxl.load_workbook(self.output_path)
            except Exception as e:
                logger.error(f"Corrupt workbook, recreating: {e}")
                os.rename(self.output_path, self.output_path + ".corrupt.bak")
                wb = openpyxl.Workbook()
        else:
            wb = openpyxl.Workbook()

        # Remove default sheet
        if "Sheet" in wb.sheetnames:
            del wb["Sheet"]

        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="4F81BD")

        for name, headers in self.SHEETS.items():
            if name not in wb.sheetnames:
                ws = wb.create_sheet(name)
                for ci, h in enumerate(headers, 1):
                    cell = ws.cell(row=1, column=ci, value=h)
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = Alignment(horizontal="center", wrap_text=True)
                for i in range(1, len(headers) + 1):
                    ws.column_dimensions[get_column_letter(i)].width = 22
        return wb

    def _existing_ids(self, ws) -> set:
        """Return set of Startup IDs already in sheet (col A, rows 2+)."""
        ids = set()
        for row in range(2, ws.max_row + 1):
            v = ws.cell(row=row, column=1).value
            if v:
                ids.add(str(v).strip())
        return ids

    def _append(self, ws, row: list):
        ws.append(row)
        for ci in range(1, len(row) + 1):
            ws.cell(ws.max_row, ci).alignment = Alignment(vertical="top", wrap_text=True)

    # ------------------------------------------------------------------
    def export_workbook(
        self,
        startups_data: list,
        founders_data: list,
        contacts_data: list,
        jobs_data: list = None,
        run_log: dict = None,
    ) -> str:
        """
        Write / update the 6-sheet Excel workbook.
        Uses startup_id for deduplication so repeated runs don't add duplicates.
        """
        wb = self._load_or_create_wb()
        today = datetime.now().strftime("%Y-%m-%d")

        # ── Build a startup_id → company lookup for linked sheets ──
        id_map = {}
        for s in startups_data:
            sid = s.get("startup_id", "")
            cmp = s.get("company", s.get("company_name", ""))
            if sid and cmp:
                id_map[cmp.lower()] = sid

        # ── Startups ────────────────────────────────────────────────
        ws_start = wb["Startups"]
        existing_ids_start = self._existing_ids(ws_start)
        for s in sorted(startups_data, key=lambda x: x.get("priority_score", 0), reverse=True):
            sid = s.get("startup_id", "")
            if sid in existing_ids_start:
                # Update Company cell if it was blank (fixes legacy empty-Company bug)
                for r in range(2, ws_start.max_row + 1):
                    if ws_start.cell(r, 1).value == sid and not ws_start.cell(r, 2).value:
                        ws_start.cell(r, 2).value = s.get("company", s.get("company_name", ""))
                continue
            cmp = s.get("company", s.get("company_name", ""))
            self._append(ws_start, [
                sid,
                cmp,
                s.get("industry", ""),
                s.get("funding_stage", ""),
                s.get("funding_amount", ""),
                s.get("investors", ""),
                s.get("city", ""),
                s.get("website", ""),
                s.get("source_url", s.get("source", "")),
            ])
            existing_ids_start.add(sid)

        # ── Funding Rounds ──────────────────────────────────────────
        ws_fund = wb["Funding Rounds"]
        existing_ids_fund = self._existing_ids(ws_fund)
        for s in startups_data:
            sid = s.get("startup_id", "")
            if sid in existing_ids_fund:
                continue
            investors = s.get("investors", "Undisclosed")
            lead, co = (investors.split(",", 1) if "," in investors else (investors, ""))
            self._append(ws_fund, [
                sid,
                s.get("company", s.get("company_name", "")),
                s.get("funding_stage", "Undisclosed"),
                s.get("funding_amount", "Undisclosed"),
                s.get("latest_funding_date", s.get("founded_year", today)),
                lead.strip() if lead else "Undisclosed",
                co.strip() if co else "—",
            ])
            existing_ids_fund.add(sid)

        # ── Founders & Leadership ───────────────────────────────────
        ws_found = wb["Founders & Leadership"]
        existing_ids_found = self._existing_ids(ws_found)
        for f in founders_data:
            sid = f.get("startup_id", id_map.get((f.get("company", "")).lower(), ""))
            if sid in existing_ids_found:
                continue
            self._append(ws_found, [
                sid,
                f.get("company", ""),
                f.get("founder_name", f.get("founder", "")),
                f.get("ceo", ""),
                f.get("position", ""),
                f.get("public_linkedin_url", ""),
                f.get("website_profile", ""),
                f.get("email", ""),
            ])
            if sid:
                existing_ids_found.add(sid)

        # ── Key Contacts ────────────────────────────────────────────
        ws_cont = wb["Key Contacts"]
        existing_ids_cont = self._existing_ids(ws_cont)
        for c in contacts_data:
            sid = c.get("startup_id", id_map.get((c.get("company", "")).lower(), ""))
            # Allow multiple contacts per startup — deduplicate by LinkedIn URL instead
            existing_li = set()
            for r in range(2, ws_cont.max_row + 1):
                v = ws_cont.cell(r, 6).value
                if v:
                    existing_li.add(str(v).strip())
            li_url = c.get("public_linkedin_url", "")
            if li_url and li_url in existing_li:
                continue
            self._append(ws_cont, [
                sid,
                c.get("company", ""),
                c.get("contact_name", ""),
                c.get("role", ""),
                c.get("department", ""),
                li_url,
                c.get("email", c.get("public_business_email", "")),
            ])

        # ── LinkedIn Profiles ───────────────────────────────────────
        ws_li = wb["LinkedIn Profiles"]
        existing_li_urls = set()
        for r in range(2, ws_li.max_row + 1):
            v = ws_li.cell(r, 5).value
            if v:
                existing_li_urls.add(str(v).strip())
        all_people = founders_data + contacts_data
        for p in all_people:
            li_url = p.get("public_linkedin_url", p.get("linkedin_url", ""))
            if not li_url or li_url in existing_li_urls:
                continue
            cmp = p.get("company", "")
            sid = p.get("startup_id", id_map.get(cmp.lower(), ""))
            self._append(ws_li, [
                sid,
                cmp,
                p.get("founder_name", p.get("contact_name", p.get("name", ""))),
                p.get("position", p.get("role", "")),
                li_url,
            ])
            existing_li_urls.add(li_url)

        # ── Logs ────────────────────────────────────────────────────
        ws_log = wb["Logs"]
        if run_log:
            self._append(ws_log, [
                run_log.get("date", today),
                run_log.get("companies_found", 0),
                run_log.get("companies_added", 0),
                run_log.get("contacts_found", 0),
                run_log.get("errors", 0),
                run_log.get("duration", ""),
                run_log.get("status", "SUCCESS"),
            ])

        # ── Save ────────────────────────────────────────────────────
        try:
            wb.save(self.output_path)
            logger.info(f"ExcelOutreachExporter: workbook saved → {self.output_path}")
        except PermissionError:
            logger.error(f"Permission denied saving Excel — close the file first: {self.output_path}")
        except Exception as e:
            logger.error(f"Failed to save workbook: {e}")

        return self.output_path

    # Backwards-compat alias so callers using .export() also work
    def export(self, **kwargs):
        return self.export_workbook(**kwargs)

    # ── Outreach Specific Methods ──────────────────────────────────────────────
    def read_outreach_state(self) -> list[dict]:
        """Reads all rows from the Outreach sheet as dictionaries."""
        wb = self._load_or_create_wb()
        ws = wb["Outreach"]
        
        headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
        records = []
        for row in range(2, ws.max_row + 1):
            record = {}
            for col_idx, header in enumerate(headers, 1):
                val = ws.cell(row, col_idx).value
                record[header] = val if val is not None else ""
            if record.get("Company"):
                records.append(record)
        return records

    def append_outreach_row(self, row_dict: dict):
        """Append a new row to the Outreach sheet."""
        wb = self._load_or_create_wb()
        ws = wb["Outreach"]
        headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
        row_data = [row_dict.get(h, "") for h in headers]
        self._append(ws, row_data)
        
        try:
            wb.save(self.output_path)
            logger.info(f"Outreach sheet updated with new contact for {row_dict.get('Company', 'Unknown')}.")
        except Exception as e:
            logger.error(f"Failed to save outreach row: {e}")

    def update_outreach_row(self, campaign_id: str, updates: dict):
        """Update an existing row in the Outreach sheet matched by Campaign ID."""
        wb = self._load_or_create_wb()
        ws = wb["Outreach"]
        headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
        
        camp_idx = -1
        for i, h in enumerate(headers, 1):
            if h == "Campaign ID":
                camp_idx = i
                break
                
        if camp_idx == -1:
            return
            
        for row in range(2, ws.max_row + 1):
            cell_val = ws.cell(row, camp_idx).value
            if cell_val and str(cell_val).strip() == campaign_id:
                for col_idx, header in enumerate(headers, 1):
                    if header in updates:
                        ws.cell(row, col_idx).value = updates[header]
                try:
                    wb.save(self.output_path)
                    logger.info(f"Updated outreach row for Campaign ID: {campaign_id}")
                except Exception as e:
                    logger.error(f"Failed to update outreach row: {e}")
                return
