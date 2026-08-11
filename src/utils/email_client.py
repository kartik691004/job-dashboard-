import smtplib
import time
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from src.config import (
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    OUTREACH_SENDER_NAME,
    OUTREACH_SENDER_EMAIL
)

logger = logging.getLogger("EmailClient")

class EmailClient:
    def __init__(self):
        self.host = SMTP_HOST
        self.port = SMTP_PORT
        self.user = SMTP_USER
        self.password = SMTP_PASSWORD
        self.sender_name = OUTREACH_SENDER_NAME
        self.sender_email = OUTREACH_SENDER_EMAIL

        if not self.user or not self.password:
            logger.warning("SMTP_USER or SMTP_PASSWORD not set in environment.")

    def send_email(self, to_email: str, subject: str, body_text: str, retries: int = 3, delay_sec: int = 5) -> bool:
        """
        Send an email via SMTP.
        Includes retry logic for transient network or rate limit errors.
        """
        if not self.user or not self.password:
            logger.error("Cannot send email: SMTP credentials are not configured.")
            return False

        msg = MIMEMultipart()
        msg["From"] = f"{self.sender_name} <{self.sender_email}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body_text, "plain"))

        for attempt in range(1, retries + 1):
            try:
                # Use starttls for port 587
                if self.port == 587:
                    server = smtplib.SMTP(self.host, self.port, timeout=15)
                    server.starttls()
                # Use SSL for port 465
                elif self.port == 465:
                    server = smtplib.SMTP_SSL(self.host, self.port, timeout=15)
                # Unencrypted / local
                else:
                    server = smtplib.SMTP(self.host, self.port, timeout=15)

                server.login(self.user, self.password)
                server.send_message(msg)
                server.quit()
                logger.info(f"Email sent successfully to {to_email}")
                return True

            except Exception as e:
                logger.warning(f"Failed to send email to {to_email} (Attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    time.sleep(delay_sec)

        logger.error(f"Failed to send email to {to_email} after {retries} attempts.")
        return False
