"""
Multi-source Indian startup discovery scraper.

Sources:
  1. Startup India public API (no key required)
  2. Apify web scraping (Inc42, YourStory, Entrackr, Product Hunt, Wellfound, VC Portfolios)
  3. RSS Feeds + Tech News

Deduplication is persisted in SQLite so repeated runs don't re-process companies.
"""
import re
import time
import logging
import sqlite3
import datetime
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.config import (
    SOURCE_STARTUP_INDIA, SOURCE_NEWS_SITES, SOURCE_PRODUCT_HUNT,
    SOURCE_WELLFOUND, SOURCE_VC_PORTFOLIOS, SOURCE_TECH_NEWS, SOURCE_LINKEDIN_JOBS,
    SOURCE_JOB_PLATFORMS, SOURCE_Y_COMBINATOR, SOURCE_FACEBOOK_ADS,
    NEWS_RSS_FEEDS, VC_PORTFOLIO_URLS, WELLFOUND_INDIA_URL,
    PRODUCT_HUNT_INDIA_TOPICS, UNICORN_EXCLUSION_LIST,
    PIPELINE_FOUNDING_YEAR_MIN, PIPELINE_EMPLOYEE_MAX, PIPELINE_TARGET_STAGES,
    LINKEDIN_JOBS_KEYWORDS, LINKEDIN_JOBS_LOOKBACK_DAYS, LINKEDIN_JOBS_MIN_POSTINGS,
    JOB_PLATFORM_SITES, JOB_PLATFORM_URLS, JOB_PLATFORM_KEYWORDS,
    JOB_PLATFORM_LOOKBACK_DAYS, JOB_PLATFORM_MIN_POSTINGS, JOB_PLATFORM_MAX_RESULTS,
    YC_RECENT_BATCHES, YC_REGIONS_FILTER, YC_MAX_COMPANIES,
    FB_ADS_SEARCH_TERMS, FB_ADS_COUNTRY, FB_ADS_MAX_ADS,
)
from src.scrapers.utils import guess_website
from src.scrapers.apify_client import ApifyClient

logger = logging.getLogger("StartupDiscovery")


# -- SQLite dedup store --------------------------------------------------------
def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_startups (
            company_name TEXT PRIMARY KEY COLLATE NOCASE,
            first_seen   TEXT,
            source       TEXT
        )
    """)
    conn.commit()
    return conn


class IndianStartupDiscoverer:
    """
    Discovers early-stage Indian startups from multiple sources.
    Filters out unicorns and strictly enforces early-stage criteria.
    """

    def __init__(self, db_path: str = "data/ftb_store.db"):
        self.db_path = db_path
        self.gemini  = None
        self.apify   = ApifyClient()
        self.conn    = _get_conn(db_path)

    def set_gemini(self, gemini_processor):
        self.gemini = gemini_processor

    # -------------------------------------------------------------------------
    def discover_recent_startups(self, limit: int = 50) -> List[Dict[str, Any]]:
        logger.info("=" * 60)
        logger.info(f"STARTUP DISCOVERY - Target: {limit} new companies")
        logger.info("=" * 60)

        raw_candidates: List[Dict] = self._run_all_sources()
        
        # Initial normalization and filtering before AI processing
        normalized_candidates = self._normalize_records(raw_candidates)
        filtered_candidates = self._filter_early_stage(normalized_candidates)
        
        unique_new = self._deduplicate(filtered_candidates, limit)

        logger.info(f"Discovery complete - {len(unique_new)} unique new startups found out of {len(raw_candidates)} total collected.")
        return unique_new

    def _run_all_sources(self) -> List[Dict[str, Any]]:
        """Run all enabled data sources in parallel and aggregate results."""
        candidates = []
        futures_map = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            if SOURCE_STARTUP_INDIA:
                futures_map[executor.submit(self._source_startup_india)] = "Startup India"
            if SOURCE_NEWS_SITES:
                futures_map[executor.submit(self._source_news_sites)] = "News Sites"
            if SOURCE_PRODUCT_HUNT:
                futures_map[executor.submit(self._source_product_hunt)] = "Product Hunt"
            if SOURCE_WELLFOUND:
                futures_map[executor.submit(self._source_wellfound)] = "Wellfound"
            if SOURCE_VC_PORTFOLIOS:
                futures_map[executor.submit(self._source_vc_portfolios)] = "VC Portfolios"
            if SOURCE_TECH_NEWS:
                futures_map[executor.submit(self._source_tech_news)] = "Tech News (RSS/Search)"
            if SOURCE_LINKEDIN_JOBS:
                futures_map[executor.submit(self._source_linkedin_jobs)] = "LinkedIn Jobs (Frequency)"
            if SOURCE_JOB_PLATFORMS:
                futures_map[executor.submit(self._source_job_platforms)] = "Job Platforms (Frequency)"
            if SOURCE_Y_COMBINATOR:
                futures_map[executor.submit(self._source_y_combinator)] = "Y Combinator"
            if SOURCE_FACEBOOK_ADS:
                futures_map[executor.submit(self._source_facebook_ads)] = "Facebook Ads"

            for future in concurrent.futures.as_completed(futures_map):
                source_name = futures_map[future]
                try:
                    result = future.result()
                    logger.info(f"Source [{source_name}] returned {len(result)} candidates")
                    candidates.extend(result)
                except Exception as e:
                    logger.error(f"Source [{source_name}] failed: {e}")

        return candidates

    # ── Source Implementations ────────────────────────────────────────────────
    
    def _source_startup_india(self) -> List[Dict[str, Any]]:
        """Fetch from Startup India Gov API."""
        return self.apify.fetch_startup_india(page_size=50)

    def _source_news_sites(self) -> List[Dict[str, Any]]:
        """Scrape known funding portals using Apify."""
        urls = [
            "https://inc42.com/tag/funding/",
            "https://inc42.com/tag/funding-alert/",
            "https://yourstory.com/tag/funding",
            "https://entrackr.com/category/news/",
            "https://economictimes.indiatimes.com/tech-startups",
            "https://www.vccircle.com/deals/",
        ]
        
        pages = self.apify.scrape_urls(urls)
        results = []
        for page in pages:
            url = page.get("url", "")
            text = page.get("text", "")
            source = "Inc42" if "inc42" in url else "YourStory" if "yourstory" in url else "Entrackr" if "entrackr" in url else "ET" if "economictimes" in url else "VCCircle" if "vccircle" in url else "News Site"
            # Simple heuristic regex to grab names from typical funding text if AI is not used for this step
            results.extend(self._parse_funding_text(text, source, url))
        
        return results

    def _source_product_hunt(self) -> List[Dict[str, Any]]:
        """Fetch Indian launches from Product Hunt."""
        return self.apify.fetch_product_hunt_india(PRODUCT_HUNT_INDIA_TOPICS)

    def _source_wellfound(self) -> List[Dict[str, Any]]:
        """Fetch Indian startups from Wellfound."""
        return self.apify.fetch_wellfound_india(WELLFOUND_INDIA_URL)

    def _source_vc_portfolios(self) -> List[Dict[str, Any]]:
        """Scrape public VC portfolio pages."""
        return self.apify.fetch_vc_portfolios(VC_PORTFOLIO_URLS)

    def _source_tech_news(self) -> List[Dict[str, Any]]:
        """Fetch and parse RSS feeds of tech news sites."""
        rss_results = self.apify.fetch_rss_feeds_parallel(NEWS_RSS_FEEDS)
        candidates = []
        for source_name, items in rss_results.items():
            for item in items:
                title = item.get("title", "")
                url = item.get("url", "")
                desc = item.get("description", "")
                parsed = self._parse_funding_text(f"{title}. {desc}", source_name, url)
                candidates.extend(parsed)
        return candidates

    def _source_linkedin_jobs(self) -> List[Dict[str, Any]]:
        """
        Discover startups from LinkedIn Jobs via DuckDuckGo search.
        Companies are ranked by internship posting frequency within the lookback window.
        This is a strong signal for early-stage startups actively growing their team.
        """
        return self.apify.fetch_linkedin_internships(
            keywords=LINKEDIN_JOBS_KEYWORDS,
            lookback_days=LINKEDIN_JOBS_LOOKBACK_DAYS,
            min_postings=LINKEDIN_JOBS_MIN_POSTINGS,
        )

    def _source_job_platforms(self) -> List[Dict[str, Any]]:
        """
        Discover startups from Indian job platforms (Instahyre, Cutshort,
        Internshala, Unstop, Indeed). Companies posting frequently within a
        short window are flagged — active hiring sprees suggest recent funding.
        """
        return self.apify.fetch_job_platform_frequency(
            platforms=JOB_PLATFORM_SITES,
            platform_urls=JOB_PLATFORM_URLS,
            keywords=JOB_PLATFORM_KEYWORDS,
            lookback_days=JOB_PLATFORM_LOOKBACK_DAYS,
            min_postings=JOB_PLATFORM_MIN_POSTINGS,
            max_results=JOB_PLATFORM_MAX_RESULTS,
        )

    def _source_y_combinator(self) -> List[Dict[str, Any]]:
        """Discover recently-funded Indian/South-Asian startups from Y Combinator."""
        return self.apify.fetch_y_combinator(
            recent_batches=YC_RECENT_BATCHES,
            regions=YC_REGIONS_FILTER,
            max_results=YC_MAX_COMPANIES,
        )

    def _source_facebook_ads(self) -> List[Dict[str, Any]]:
        """Discover startups running Facebook/Meta ads."""
        return self.apify.fetch_facebook_ads(
            search_terms=FB_ADS_SEARCH_TERMS,
            country=FB_ADS_COUNTRY,
            max_results=FB_ADS_MAX_ADS,
        )

    # ── Filtering & Normalization ─────────────────────────────────────────────

    def _parse_funding_text(self, text: str, source: str, url: str) -> List[Dict[str, Any]]:
        """Extract basic info using regex if Gemini isn't available."""
        # We rely mostly on Gemini for parsing complex unstructured text, but need a fallback
        # if the text is from a generic page
        if not text: return []
        
        _AMOUNT_RE = re.compile(r"(?:USD\s*)?\$[\d,\.]+\s*[MBKmk](?:illion|n)?|Rs.[\d,\.]+\s*(?:crore|lakh|Cr|cr)?|[\d,\.]+\s*(?:million|billion|crore|lakh)\s*(?:USD|INR|rupees)?", re.IGNORECASE)
        _STAGE_RE = re.compile(r"\b(Pre-Seed|Seed|Pre-Series [A-D]|Series [A-E]\+?|Series [A-E]|Growth|Unicorn|IPO|Bridge|Angel)\b", re.IGNORECASE)
        
        candidates = []
        # Pattern: Company raises $X in Series Y
        m_fund = re.search(r"([A-Z][a-zA-Z0-9\.\-&]{2,30}(?:\s+[A-Z][a-zA-Z0-9\.\-&]{2,30}){0,3})\s+(?:raises?|bags?|secures?|closes?|gets?|nets?)\s+", text, re.IGNORECASE)
        if m_fund:
            company = m_fund.group(1).strip()
            amount_m = _AMOUNT_RE.search(text)
            stage_m = _STAGE_RE.search(text)
            candidates.append({
                "company_name": company,
                "website": guess_website(company),
                "industry": "Technology",
                "city": "India",
                "state": "",
                "funding_stage": stage_m.group(0).title() if stage_m else "Undisclosed",
                "funding_amount": amount_m.group(0).strip() if amount_m else "Undisclosed",
                "latest_funding_date": datetime.date.today().isoformat(),
                "investors": "See article",
                "founding_year": "",
                "employee_count": "",
                "company_description": text[:200],
                "source": source,
                "source_url": url,
            })
        return candidates

    def _normalize_records(self, raw_candidates: List[Dict]) -> List[Dict]:
        """Normalize structure of raw candidates."""
        normalized = []
        for cand in raw_candidates:
            # Basic validation
            name = cand.get("company_name", "").strip()
            if not name or len(name) < 2 or len(name) > 60:
                continue
                
            # Use Gemini to normalize name if available, else basic cleanup
            if self.gemini:
                # Name normalization will be handled in batch by process_and_clean_startups
                pass
            
            # Remove legal suffixes roughly
            name = re.sub(r'(?i)\s+(pvt|private|ltd|limited|inc|corp|corporation|llc|co)\.?$', '', name).strip()
            cand["company_name"] = name
            
            # Ensure all keys exist
            for key in ["company_name", "website", "industry", "city", "state", 
                        "funding_stage", "funding_amount", "latest_funding_date", 
                        "investors", "founding_year", "employee_count", 
                        "company_description", "source", "source_url"]:
                if key not in cand:
                    cand[key] = ""
                    
            if not cand["website"]:
                cand["website"] = guess_website(name)
            if not cand["latest_funding_date"]:
                cand["latest_funding_date"] = datetime.date.today().isoformat()
                
            normalized.append(cand)
            
        return normalized

    def _filter_early_stage(self, candidates: List[Dict]) -> List[Dict]:
        """Filter out unicorns, late-stage, old, or massive companies."""
        filtered = []
        
        target_stages_lower = [s.lower() for s in PIPELINE_TARGET_STAGES]
        
        for cand in candidates:
            name_lower = cand.get("company_name", "").lower()
            
            # 1. Unicorn Exclusion
            if name_lower in UNICORN_EXCLUSION_LIST:
                logger.debug(f"Filtered out known unicorn: {cand.get('company_name')}")
                continue
                
            # 2. Stage Filtering
            stage = cand.get("funding_stage", "").lower()
            if stage and stage != "undisclosed" and stage != "vc-backed":
                # Only strictly filter if stage is known. If undisclosed, we let it pass to AI scoring
                is_target = any(t in stage for t in target_stages_lower)
                is_late = any(l in stage for l in ["series c", "series d", "series e", "unicorn", "ipo", "growth", "late"])
                if is_late or (not is_target and stage):
                    logger.debug(f"Filtered out late-stage: {cand.get('company_name')} ({stage})")
                    continue
                    
            # 3. Founding Year
            year_str = cand.get("founding_year", "")
            if year_str and year_str.isdigit():
                if int(year_str) < PIPELINE_FOUNDING_YEAR_MIN:
                    logger.debug(f"Filtered out old company: {cand.get('company_name')} ({year_str})")
                    continue
                    
            # 4. Employee Count
            emp_str = cand.get("employee_count", "")
            if emp_str:
                # E.g., "51-200" or "500+"
                nums = re.findall(r'\d+', emp_str)
                if nums:
                    max_emp = max([int(n) for n in nums])
                    # If max is > 200, we might filter it out. PIPELINE_EMPLOYEE_MAX is default 100
                    # Let's be a bit lenient if it's a range like 51-200.
                    if max_emp > max(200, PIPELINE_EMPLOYEE_MAX * 2):
                        logger.debug(f"Filtered out large company: {cand.get('company_name')} ({emp_str} employees)")
                        continue
                        
            filtered.append(cand)
            
        return filtered

    def _deduplicate(self, candidates: List[Dict], limit: int) -> List[Dict]:
        """SQLite-backed deduplication. Returns only unique, unseen startups."""
        unique_new = []
        seen_now = set()

        for startup in candidates:
            name = startup.get("company_name", "").strip()
            name_lower = name.lower()
            
            if not name or name_lower in seen_now:
                continue
            seen_now.add(name_lower)

            # Check DB
            row = self.conn.execute(
                "SELECT 1 FROM seen_startups WHERE company_name = ?", (name,)
            ).fetchone()
            
            if not row:
                self.conn.execute(
                    "INSERT OR IGNORE INTO seen_startups VALUES (?,?,?)",
                    (name, datetime.date.today().isoformat(), startup.get("source", "web")),
                )
                self.conn.commit()
                unique_new.append(startup)
                
            if len(unique_new) >= limit:
                break
                
        return unique_new
