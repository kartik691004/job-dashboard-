import logging
import uuid
import time
from datetime import datetime, timedelta

from src.config import (
    OUTREACH_DAILY_LIMIT,
    OUTREACH_FOLLOWUP_DELAY_DAYS,
    OUTREACH_MAX_FOLLOWUPS,
    OUTREACH_DRY_RUN
)
from src.utils.email_client import EmailClient
from src.ai.gemini_processor import GeminiAIProcessor
from src.exporters.excel_exporter import ExcelOutreachExporter

logger = logging.getLogger("OutreachWorkflow")

class OutreachWorkflow:
    def __init__(self):
        self.email_client = EmailClient()
        self.gemini = GeminiAIProcessor()
        self.exporter = ExcelOutreachExporter()

    def run_initial_outreach(self) -> dict:
        """
        Workflow 2 (5 AM Daily):
        - Read startups
        - Generate outreach sequence
        - Send initial email
        - Record to Outreach sheet
        """
        logger.info("Starting Workflow 2: Initial Outreach...")
        
        # We need to find startups in "Startups" sheet that aren't in "Outreach" sheet yet
        wb = self.exporter._load_or_create_wb()
        
        # Load existing outreach state to know who we've already reached out to
        outreach_state = self.exporter.read_outreach_state()
        contacted_companies = {row.get("Company", "").lower() for row in outreach_state}
        
        # Load startups
        ws_startups = wb["Startups"]
        startups_headers = [ws_startups.cell(1, c).value for c in range(1, ws_startups.max_column + 1)]
        
        # Load Key Contacts
        ws_contacts = wb["Key Contacts"]
        contacts_headers = [ws_contacts.cell(1, c).value for c in range(1, ws_contacts.max_column + 1)]
        
        # Build mapping of Company -> [Contacts]
        contacts_by_company = {}
        for row in range(2, ws_contacts.max_row + 1):
            record = {}
            for col_idx, header in enumerate(contacts_headers, 1):
                record[header] = ws_contacts.cell(row, col_idx).value or ""
            comp_lower = record.get("Company", "").lower()
            if comp_lower:
                if comp_lower not in contacts_by_company:
                    contacts_by_company[comp_lower] = []
                contacts_by_company[comp_lower].append(record)

        # Process startups
        sent_count = 0
        errors_count = 0
        
        for row in range(2, ws_startups.max_row + 1):
            if sent_count >= OUTREACH_DAILY_LIMIT:
                logger.info("Daily outreach limit reached.")
                break
                
            record = {}
            for col_idx, header in enumerate(startups_headers, 1):
                record[header] = ws_startups.cell(row, col_idx).value or ""
                
            company = record.get("Company", "")
            comp_lower = company.lower()
            
            if not company or comp_lower in contacted_companies:
                continue
                
            # Find a contact
            contacts = contacts_by_company.get(comp_lower, [])
            valid_contacts = [c for c in contacts if c.get("Email")]
            if not valid_contacts:
                continue
                
            # Pick primary contact
            contact = valid_contacts[0]
            contact_name = contact.get("Contact Name", "")
            contact_email = contact.get("Email", "")
            contact_role = contact.get("Role", "Founder")
            
            logger.info(f"Generating AI email for {company} -> {contact_email}")
            
            # Generate email
            email_data = self.gemini.generate_personalized_outreach(
                company_name=company,
                contact_name=contact_name,
                role=contact_role,
                funding_stage=record.get("Funding Stage", "Startup"),
                ai_summary=record.get("Description", "")
            )
            
            subject = email_data.get("email_subject", f"Opportunity for {company}")
            body = email_data.get("initial_pitch", "")
            
            if not body:
                logger.error(f"Failed to generate email body for {company}")
                errors_count += 1
                continue
                
            # Send email
            if OUTREACH_DRY_RUN:
                logger.info(f"[DRY RUN] Skipping SMTP send for {company}. Saving draft to sheet.")
                success = True
            else:
                success = self.email_client.send_email(contact_email, subject, body)
            
            if success:
                campaign_id = str(uuid.uuid4())
                today_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # Update outreach state
                outreach_record = {
                    "Company": company,
                    "Contact Name": contact_name,
                    "Role": contact_role,
                    "Business Email": contact_email,
                    "Date Added": today_str,
                    "Initial Email Sent": "Draft" if OUTREACH_DRY_RUN else "Yes",
                    "Follow-up Sent": 0,
                    "Reply Status": "No Reply",
                    "Last Activity": today_str,
                    "Campaign ID": campaign_id,
                    "Subject": subject,
                    "Body": body,
                    "Notes": "Dry run - Draft saved" if OUTREACH_DRY_RUN else "Initial email sent",
                }
                self.exporter.append_outreach_row(outreach_record)
                contacted_companies.add(comp_lower)
                sent_count += 1
                time.sleep(2) # Rate limit
            else:
                errors_count += 1

        logger.info(f"Initial Outreach complete. Sent: {sent_count}, Errors: {errors_count}")
        return {"sent": sent_count, "errors": errors_count}

    def run_followup_outreach(self) -> dict:
        """
        Workflow 3 (Follow-up):
        - Check for no replies
        - Wait OUTREACH_FOLLOWUP_DELAY_DAYS
        - Send follow up
        """
        logger.info("Starting Workflow 3: Follow-up Outreach...")
        
        outreach_state = self.exporter.read_outreach_state()
        sent_count = 0
        errors_count = 0
        now = datetime.now()
        
        for row in outreach_state:
            reply_status = row.get("Reply Status", "")
            campaign_id = row.get("Campaign ID", "")
            
            # Skip if they replied or campaign is invalid
            if reply_status.lower() == "replied" or not campaign_id:
                continue
                
            followups_sent = 0
            try:
                followups_sent = int(row.get("Follow-up Sent", 0))
            except:
                pass
                
            if followups_sent >= OUTREACH_MAX_FOLLOWUPS:
                continue
                
            last_activity_str = row.get("Last Activity", "")
            if not last_activity_str:
                continue
                
            try:
                last_activity = datetime.strptime(last_activity_str, "%Y-%m-%d %H:%M:%S")
            except:
                continue
                
            if (now - last_activity).days >= OUTREACH_FOLLOWUP_DELAY_DAYS:
                company = row.get("Company", "")
                contact_name = row.get("Contact Name", "")
                email = row.get("Business Email", "")
                
                logger.info(f"Sending follow-up {followups_sent + 1} to {company} ({email})")
                
                # Regenerate or use generic follow up (since we didn't store the AI generated day3/day7 string in Outreach)
                first_name = contact_name.split()[0] if contact_name else "there"
                
                if followups_sent == 0:
                    subject = f"Following up - Opportunity for {company}"
                    body = f"Hi {first_name}, following up on my note from earlier. Happy to share a quick case study on how we've helped similar startups. Would Tuesday or Wednesday work for a brief call?"
                else:
                    subject = f"Last note - {company}"
                    body = f"Hi {first_name}, last note from my side - if the timing isn't right, no worries at all. Feel free to reach out whenever works best for {company}. Wishing you a great quarter!"

                if OUTREACH_DRY_RUN:
                    logger.info(f"[DRY RUN] Skipping SMTP follow-up for {company}. Saving draft to sheet.")
                    success = True
                else:
                    success = self.email_client.send_email(email, subject, body)
                
                if success:
                    updates = {
                        "Follow-up Sent": followups_sent + 1,
                        "Last Activity": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "Notes": f"[DRY RUN] Follow-up {followups_sent + 1} drafted" if OUTREACH_DRY_RUN else f"Follow-up {followups_sent + 1} sent",
                        "Reply Status": "Replied" if followups_sent + 1 >= OUTREACH_MAX_FOLLOWUPS else "No Reply", # Automatically end campaign
                        "Subject": subject,
                        "Body": body
                    }
                    self.exporter.update_outreach_row(campaign_id, updates)
                    sent_count += 1
                    time.sleep(2)
                else:
                    errors_count += 1
                    
        logger.info(f"Follow-up Outreach complete. Sent: {sent_count}, Errors: {errors_count}")
        return {"sent": sent_count, "errors": errors_count}
