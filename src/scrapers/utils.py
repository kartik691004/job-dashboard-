"""
HTTP utilities for resilient web scraping.
Handles user-agent rotation, retries, rate limiting, and HTML text extraction.
"""
import re
import time
import random
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

ACCEPT_HEADERS = [
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
]


def get_headers(referer: str = None) -> dict:
    """Build realistic browser-like headers."""
    headers = {
        "User-Agent":        random.choice(USER_AGENTS),
        "Accept":            random.choice(ACCEPT_HEADERS),
        "Accept-Language":   "en-US,en;q=0.9,hi;q=0.8",
        "Accept-Encoding":   "gzip, deflate, br",
        "Connection":        "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":    "document",
        "Sec-Fetch-Mode":    "navigate",
        "Sec-Fetch-Site":    "cross-site" if referer else "none",
        "Cache-Control":     "max-age=0",
        "DNT":               "1",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def safe_get(
    url: str,
    timeout: int = 20,
    retries: int = 3,
    delay_range: tuple = (2.0, 5.0),
    referer: str = None,
) -> Optional[requests.Response]:
    """
    GET with retry logic, random delay, and anti-bot headers.
    Returns Response on success, None on persistent failure.
    """
    for attempt in range(retries):
        try:
            time.sleep(random.uniform(*delay_range))
            session = requests.Session()
            verify_ssl = True
            try:
                resp = session.get(
                    url,
                    headers=get_headers(referer),
                    timeout=timeout,
                    allow_redirects=True,
                    verify=verify_ssl,
                )
            except requests.exceptions.SSLError:
                # Never fall back to verify=False; a cert failure is a hard stop.
                logger.debug(f"SSL verify failed for {url[:60]}, aborting retries.")
                raise
            if resp.status_code == 200:
                return resp
            elif resp.status_code == 429:
                wait = 20 + attempt * 15
                logger.warning(f"Rate limited ({url[:60]}). Sleeping {wait}s...")
                time.sleep(wait)
            elif resp.status_code in (403, 406, 503):
                logger.warning(f"Blocked HTTP {resp.status_code} -> {url[:60]}")
                return None
            else:
                logger.debug(f"HTTP {resp.status_code} -> {url[:60]}")
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout [{attempt+1}/{retries}] -> {url[:60]}")
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"ConnError [{attempt+1}/{retries}] -> {url[:60]}: {e}")
            time.sleep(5)
        except Exception as e:
            logger.warning(f"Request error [{attempt+1}/{retries}] -> {url[:60]}: {e}")
    return None


def extract_text(html: str, max_chars: int = 6000) -> str:
    """Strip HTML tags and return clean text, truncated to max_chars."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text[:max_chars]
    except Exception:
        # Fallback: crude regex strip
        clean = re.sub(r"<[^>]+>", " ", html)
        clean = re.sub(r"\s{2,}", " ", clean).strip()
        return clean[:max_chars]


def careers_url_candidates(website: str) -> list:
    """Return ordered list of probable careers-page URLs for a company website."""
    base = website.rstrip("/").rstrip("#")
    if not base.startswith("http"):
        base = "https://" + base
    return [
        f"{base}/careers",
        f"{base}/jobs",
        f"{base}/careers/",
        f"{base}/join-us",
        f"{base}/work-with-us",
        f"{base}/about/careers",
        f"{base}/company/careers",
        f"{base}/hiring",
        f"{base}/open-positions",
    ]


def guess_website(company_name: str) -> str:
    """Best-guess website from company name."""
    slug = re.sub(r"[^a-z0-9]", "", company_name.lower())
    return f"https://{slug}.com"
