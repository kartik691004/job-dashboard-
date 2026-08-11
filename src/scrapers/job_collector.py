"""
Real job & internship collector.

For each startup it:
  1. Checks the company's own /careers or /jobs page directly
  2. Searches DuckDuckGo for the company on:
       - Internshala  - Wellfound  - Naukri  - Unstop
       - LinkedIn Jobs  - Indeed India  - YC Jobs
  3. Scrapes Internshala and Wellfound directly for structured data
  4. Uses Gemini AI (if available) to parse unstructured job listings

All scrapers are resilient - graceful failure, delay between requests.
"""
import re
import time
import logging
import datetime
from typing import List, Dict, Any, Optional

from src.scrapers.utils        import safe_get, extract_text, careers_url_candidates
from src.scrapers.search_helper import ddg_urls_for_company_jobs, ddg_text

logger = logging.getLogger("JobCollector")

_JOB_TYPE_RE   = re.compile(r"\b(intern(?:ship)?|full.?time|part.?time|contract|freelance)\b", re.I)
_DATE_RE       = re.compile(r"\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\w+ \d{1,2},?\s*\d{4}|\d{4}-\d{2}-\d{2})\b")
_SALARY_RE     = re.compile(r"(?:Rs.|INR|Rs\.?)\s?[\d,]+(?:\s*[--]\s*[\d,]+)?\s*(?:LPA|per annum|/month|pm)?", re.I)


def _classify_job_type(text: str) -> str:
    m = _JOB_TYPE_RE.search(text)
    if not m:
        return "Full-Time"
    t = m.group(0).lower()
    if "intern" in t:
        return "Internship"
    return "Full-Time"


def _clean_location(text: str) -> str:
    """Extract location tokens from job listing text."""
    known = [
        "Bengaluru", "Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune",
        "Chennai", "Kolkata", "Gurgaon", "Noida", "Remote", "Work from Home",
        "WFH", "Hybrid", "Ahmedabad", "Jaipur", "Kochi",
    ]
    found = [k for k in known if k.lower() in text.lower()]
    return ", ".join(found[:2]) if found else "India"


class JobCollector:
    """
    Discovers real open job/internship listings for Indian startups
    from multiple portals using free web scraping + DuckDuckGo search.
    """

    def __init__(self):
        self.gemini = None

    def set_gemini(self, gemini_processor):
        self.gemini = gemini_processor

    # -------------------------------------------------------------------------
    def discover_jobs(self, company_name: str, website: str) -> List[Dict[str, Any]]:
        logger.info(f"Collecting jobs for: {company_name}")
        all_jobs: List[Dict] = []

        # -- 1. Careers page ---------------------------------------------------
        careers_jobs = self._scrape_careers_page(company_name, website)
        all_jobs.extend(careers_jobs)

        # -- 2. Internshala ----------------------------------------------------
        all_jobs.extend(self._scrape_internshala(company_name))

        # -- 3. Wellfound ------------------------------------------------------
        all_jobs.extend(self._scrape_wellfound(company_name))

        # -- 4. Naukri (via DDG + snippet parse) ------------------------------
        all_jobs.extend(self._search_naukri(company_name))

        # -- 5. Unstop ---------------------------------------------------------
        all_jobs.extend(self._search_unstop(company_name))

        # -- 6. Broad DDG search (LinkedIn Jobs, Indeed, YC, etc.) -------------
        all_jobs.extend(self._ddg_broad_search(company_name))

        # Deduplicate by role name
        seen_roles = set()
        unique: List[Dict] = []
        for j in all_jobs:
            key = f"{j.get('role','').lower()[:40]}-{j.get('type','')}"
            if key not in seen_roles:
                seen_roles.add(key)
                j["company"] = company_name
                unique.append(j)

        logger.info(f"{company_name}: {len(unique)} unique job listings found")
        return unique

    # -- Scrapers --------------------------------------------------------------
    def _scrape_careers_page(self, company: str, website: str) -> List[Dict]:
        jobs = []
        for url in careers_url_candidates(website)[:4]:
            resp = safe_get(url, delay_range=(2, 4))
            if not resp:
                continue
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "lxml")
                page_text = extract_text(resp.text, max_chars=8000)

                # Generic job card patterns
                job_els = (
                    soup.find_all(class_=re.compile(r"job|career|position|opening|role", re.I), limit=20)
                    or soup.find_all(["li", "div"], string=re.compile(r"engineer|manager|analyst|developer|intern|scientist", re.I), limit=20)
                )
                for el in job_els[:10]:
                    text = el.get_text(separator=" ", strip=True)
                    if len(text) < 10 or len(text) > 300:
                        continue
                    link_el = el.find("a", href=True)
                    apply_link = link_el["href"] if link_el else url
                    if apply_link and not apply_link.startswith("http"):
                        apply_link = website.rstrip("/") + "/" + apply_link.lstrip("/")
                    jobs.append({
                        "role":        text[:80],
                        "type":        _classify_job_type(text),
                        "location":    _clean_location(page_text),
                        "apply_link":  apply_link,
                        "job_source":  "Company Careers Page",
                        "date_posted": datetime.date.today().isoformat(),
                        "required_skills": "",
                    })

                # If no cards found but page has job keywords, use Gemini to parse
                if not jobs and self.gemini and any(kw in page_text.lower() for kw in ["engineer", "manager", "apply", "opening"]):
                    ai_jobs = self.gemini.extract_jobs_from_text(page_text, company, url)
                    jobs.extend(ai_jobs)

                if jobs:
                    break  # Found jobs, no need to try other career URLs
            except Exception as e:
                logger.debug(f"Careers page parse error {url}: {e}")
        return jobs

    def _scrape_internshala(self, company: str) -> List[Dict]:
        jobs = []
        slug = re.sub(r"[^a-z0-9\-]", "-", company.lower()).strip("-")
        urls = [
            f"https://internshala.com/internships/keywords-{slug}/",
            f"https://internshala.com/jobs/keywords-{slug}/",
        ]
        for url in urls:
            resp = safe_get(url, delay_range=(2, 4))
            if not resp:
                continue
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "lxml")
                # Internshala uses .internship_meta or .individual_internship
                cards = soup.find_all(class_=re.compile(r"individual_internship|job-container|internship-listing", re.I), limit=10)
                for card in cards:
                    role_el  = card.find(class_=re.compile(r"profile|job-title|title", re.I)) or card.find("h3")
                    role     = role_el.get_text(strip=True) if role_el else "Role"
                    loc_el   = card.find(class_=re.compile(r"location|city", re.I))
                    location = loc_el.get_text(strip=True) if loc_el else "India"
                    link_el  = card.find("a", href=True)
                    href     = link_el["href"] if link_el else url
                    if href and not href.startswith("http"):
                        href = "https://internshala.com" + href
                    job_type = "Internship" if "internship" in url else "Full-Time"
                    jobs.append({
                        "role":        role[:100],
                        "type":        job_type,
                        "location":    location[:80],
                        "apply_link":  href,
                        "job_source":  "Internshala",
                        "date_posted": datetime.date.today().isoformat(),
                        "required_skills": "",
                    })
            except Exception as e:
                logger.debug(f"Internshala parse error: {e}")
        logger.info(f"Internshala -> {len(jobs)} jobs for {company}")
        return jobs

    def _scrape_wellfound(self, company: str) -> List[Dict]:
        jobs = []
        slug = re.sub(r"\s+", "-", company.lower().strip())
        slug = re.sub(r"[^a-z0-9\-]", "", slug)
        url  = f"https://wellfound.com/company/{slug}/jobs"
        resp = safe_get(url, delay_range=(3, 6))
        if not resp:
            return jobs
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "lxml")
            # Wellfound uses various class patterns
            role_links = soup.find_all("a", href=re.compile(r"/jobs/\d+"), limit=15)
            for link in role_links:
                role = link.get_text(strip=True) or "Role"
                href = link["href"]
                if href and not href.startswith("http"):
                    href = "https://wellfound.com" + href
                parent_text = link.find_parent().get_text(separator=" ", strip=True) if link.find_parent() else ""
                jobs.append({
                    "role":        role[:100],
                    "type":        _classify_job_type(role + " " + parent_text),
                    "location":    _clean_location(parent_text),
                    "apply_link":  href,
                    "job_source":  "Wellfound",
                    "date_posted": datetime.date.today().isoformat(),
                    "required_skills": "",
                })
        except Exception as e:
            logger.debug(f"Wellfound parse error: {e}")
        logger.info(f"Wellfound -> {len(jobs)} jobs for {company}")
        return jobs

    def _search_naukri(self, company: str) -> List[Dict]:
        """Search Naukri via DDG and parse snippets."""
        jobs = []
        results = ddg_text(f'site:naukri.com "{company}" jobs India 2025', max_results=5)
        for r in results:
            title   = r.get("title", "")
            snippet = r.get("body", "")
            url     = r.get("href", "")
            if not title or not url:
                continue
            # Clean Naukri title: "Software Engineer - Company Name - naukri.com"
            role = re.sub(r"\s*[-|]\s*naukri\.com.*$", "", title, flags=re.I).strip()
            role = re.sub(rf"\s*[-|]\s*{re.escape(company)}.*$", "", role, flags=re.I).strip()
            if len(role) < 3:
                continue
            jobs.append({
                "role":        role[:100],
                "type":        _classify_job_type(title + " " + snippet),
                "location":    _clean_location(snippet),
                "apply_link":  url,
                "job_source":  "Naukri",
                "date_posted": datetime.date.today().isoformat(),
                "required_skills": "",
            })
        return jobs

    def _search_unstop(self, company: str) -> List[Dict]:
        """Search Unstop via DDG for internship/competition listings."""
        jobs = []
        results = ddg_text(f'site:unstop.com "{company}" internship OR jobs', max_results=4)
        for r in results:
            title   = r.get("title", "")
            snippet = r.get("body", "")
            url     = r.get("href", "")
            if not title or "unstop.com" not in url:
                continue
            jobs.append({
                "role":        title[:100],
                "type":        "Internship" if "intern" in title.lower() else "Full-Time",
                "location":    _clean_location(snippet),
                "apply_link":  url,
                "job_source":  "Unstop",
                "date_posted": datetime.date.today().isoformat(),
                "required_skills": "",
            })
        return jobs

    def _ddg_broad_search(self, company: str) -> List[Dict]:
        """Broad DDG search hitting LinkedIn Jobs, Indeed, YC Jobs, etc."""
        jobs = []
        search_results = ddg_urls_for_company_jobs(company)
        portal_map = {
            "linkedin.com/jobs": "LinkedIn Jobs",
            "linkedin.com/in":   "LinkedIn",
            "indeed.com":        "Indeed",
            "ycombinator.com":   "YC Jobs",
            "glassdoor.com":     "Glassdoor",
            "foundit.in":        "Foundit",
            "monster.in":        "Monster India",
            "shine.com":         "Shine",
            "hirist.com":        "Hirist",
        }
        for r in search_results:
            url     = r.get("href", "")
            title   = r.get("title", "")
            snippet = r.get("body", "")

            # Skip already scraped portals
            if any(x in url for x in ["internshala", "naukri", "wellfound", "unstop"]):
                continue

            source = "Web"
            for domain, label in portal_map.items():
                if domain in url:
                    source = label
                    break

            role = re.sub(r"\s*[-|at]\s*" + re.escape(company) + r".*$", "", title, flags=re.I).strip()
            role = role[:100] or title[:80]
            if len(role) < 3:
                continue

            jobs.append({
                "role":        role,
                "type":        _classify_job_type(title + " " + snippet),
                "location":    _clean_location(snippet),
                "apply_link":  url,
                "job_source":  source,
                "date_posted": datetime.date.today().isoformat(),
                "required_skills": "",
            })
        return jobs
