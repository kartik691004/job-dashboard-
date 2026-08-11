"""
Leadership & Business Contact Crawler.

Uses Apify to asynchronously crawl company websites and extract leadership names,
public emails, LinkedIn profiles, and careers/team page links.
"""
import re
import logging
from typing import List, Dict, Any, Optional

from src.scrapers.utils import careers_url_candidates
from src.scrapers.apify_client import ApifyClient
from src.scrapers.search_helper import ddg_urls_for_leadership

logger = logging.getLogger("LeadershipCrawler")

class LeadershipHiringScraper:
    """
    Crawls startup websites and LinkedIn via Apify/DDG to find founders,
    leadership, HR contacts, and business emails.
    """

    def __init__(self):
        self.gemini = None
        self.apify = ApifyClient()

    def set_gemini(self, gemini_processor):
        self.gemini = gemini_processor

    def scrape_for_startup(self, startup: Dict[str, Any]) -> Dict[str, Any]:
        """
        Orchestrates scraping for a single startup.
        Returns a dict with 'leadership', 'contacts', and 'hiring' info.
        """
        company_name = startup.get("company_name", "")
        website = startup.get("website", "")
        
        logger.info(f"Crawling enrichment data for: {company_name}")
        
        results = {
            "leadership": {
                "founders": [],
                "ceo": "",
                "cto": "",
                "linkedin_url": "",
                "team_page_url": ""
            },
            "contacts": [],  # List of {name, role, email, source, linkedin}
            "hiring": {
                "careers_page_url": ""
            }
        }
        
        if not website:
            # Fallback to just DDG LinkedIn search if no website
            self._enrich_via_search(company_name, results)
            return results

        # 1. Crawl website via Apify
        pages = self.apify.crawl_company_pages(website)
        
        # 2. Extract structured data from pages
        all_text = ""
        emails = set()
        for page in pages:
            url = page.get("url", "")
            url_lower = url.lower()
            text = page.get("text", "")
            page_emails = page.get("emails", [])
            links = page.get("links", [])
            
            all_text += f"\n--- Page: {url} ---\n{text[:2000]}\n"
            emails.update(page_emails)
            
            # Identify key pages
            if "career" in url_lower or "job" in url_lower:
                results["hiring"]["careers_page_url"] = url
            elif "team" in url_lower or "about" in url_lower or "founder" in url_lower:
                results["leadership"]["team_page_url"] = url
                
            # Extract company LinkedIn
            for link in links:
                href = link.get("href", "")
                if "linkedin.com/company" in href:
                    results["leadership"]["linkedin_url"] = href
        
        # Add found generic emails
        for email in emails:
            if any(p in email.lower() for p in ["contact", "info", "hello", "support", "sales", "press", "careers", "jobs"]):
                results["contacts"].append({
                    "name": "General Contact",
                    "role": "Business/Support",
                    "email": email,
                    "source": "website",
                    "linkedin_url": ""
                })
        
        # 3. Use Gemini to extract leadership from page text
        if self.gemini and all_text.strip():
            logger.debug(f"Using Gemini to extract leadership from website text ({len(all_text)} chars)")
            ai_data = self.gemini.extract_leadership_from_text(company_name, all_text[:10000])
            if ai_data:
                # Merge AI extracted data
                if "founders" in ai_data:
                    results["leadership"]["founders"].extend(ai_data["founders"])
                if ai_data.get("ceo"):
                    results["leadership"]["ceo"] = ai_data["ceo"]
                if ai_data.get("cto"):
                    results["leadership"]["cto"] = ai_data["cto"]
                
                # Add specific people contacts
                if "people" in ai_data:
                    for person in ai_data["people"]:
                        results["contacts"].append({
                            "name": person.get("name", ""),
                            "role": person.get("role", ""),
                            "email": person.get("email", ""),
                            "source": "website_ai",
                            "linkedin_url": person.get("linkedin_url", "")
                        })

        # 4. Fallback/supplement with DDG LinkedIn search
        self._enrich_via_search(company_name, results)
        
        # Deduplicate founders
        results["leadership"]["founders"] = list(set(f for f in results["leadership"]["founders"] if f))
        
        return results
        
    def _enrich_via_search(self, company_name: str, results: Dict[str, Any]):
        """Supplement leadership data using LinkedIn profile searches (Apify or DDG fallback)."""
        if results["leadership"]["ceo"] and results["leadership"]["cto"] and results["leadership"]["founders"]:
            return # Already have good data
            
        profiles = self.apify.search_linkedin_profiles(f'"{company_name}" founder OR CEO OR CTO')
        
        for profile in profiles:
            name = profile.get("name", "").split("-")[0].strip()
            headline = profile.get("headline", "").lower()
            url = profile.get("url", "")
            
            if not name or "linkedin" in name.lower():
                continue
                
            is_founder = "founder" in headline or "co-founder" in headline
            is_ceo = "ceo" in headline or "chief executive" in headline
            is_cto = "cto" in headline or "chief technology" in headline
            
            if is_founder:
                results["leadership"]["founders"].append(name)
            if is_ceo and not results["leadership"]["ceo"]:
                results["leadership"]["ceo"] = name
            if is_cto and not results["leadership"]["cto"]:
                results["leadership"]["cto"] = name
                
            if is_founder or is_ceo or is_cto:
                # Check if this person is already in contacts
                exists = any(c["name"] == name for c in results["contacts"])
                if not exists:
                    role = []
                    if is_founder: role.append("Founder")
                    if is_ceo: role.append("CEO")
                    if is_cto: role.append("CTO")
                    
                    results["contacts"].append({
                        "name": name,
                        "role": ", ".join(role),
                        "email": "",
                        "source": "linkedin_search",
                        "linkedin_url": url
                    })
