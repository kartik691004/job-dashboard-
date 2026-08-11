"""
Apify API client for multi-source startup data collection.

Wraps the official apify-client SDK with:
  - Rate limiting and credit-aware batching
  - Retry logic with exponential backoff
  - Actor run management (sync + async)
  - Dataset result fetching
  - Convenience methods for each data source

Falls back to direct HTTP scraping when Apify token is not configured.
"""
import os
import re
import json
import time
import logging
import asyncio
import threading
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger("ApifyClient")

# Serialise actor launches: the Apify FREE plan caps total concurrent run memory
# (16 GB). web-scraper actors consume ~4 GB each, so more than 3-4 concurrent
# runs reliably trips "You will exceed the memory limit" errors.
_ACTOR_LAUNCH_SEMAPHORE = threading.BoundedSemaphore(3)


class ApifyClient:
    """
    Real Apify API integration for structured web data collection.
    Falls back to direct HTTP/RSS when token is not available.
    """

    def __init__(self, api_token: str = "", max_concurrent: int = 5, crawl_timeout: int = 120):
        self.api_token = api_token or os.getenv("APIFY_API_TOKEN", "")
        self.max_concurrent = max_concurrent
        self.crawl_timeout = crawl_timeout
        self._client = None
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

        if self.api_token:
            try:
                from apify_client import ApifyClient as _AC
                self._client = _AC(self.api_token)
                logger.info("Apify client initialised with API token.")
            except ImportError:
                logger.warning(
                    "apify-client package not installed. "
                    "Run: pip install apify-client. Falling back to direct HTTP."
                )
            except Exception as e:
                logger.error(f"Apify client init failed: {e}")
        else:
            logger.info("No APIFY_API_TOKEN set — using RSS + direct HTTP fallback sources.")

    @property
    def is_apify_available(self) -> bool:
        return self._client is not None

    # ── Apify Actor Execution ────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((requests.exceptions.ConnectionError, TimeoutError)),
        reraise=True,
    )
    def run_actor(
        self,
        actor_id: str,
        run_input: Dict[str, Any],
        timeout_secs: int = None,
    ) -> List[Dict[str, Any]]:
        """
        Run an Apify actor synchronously and return its dataset items.
        Waits and retries when the account's concurrent memory limit is hit
        (common on free plans), so actors eventually get a slot instead of failing.
        """
        if not self._client:
            logger.warning(f"Apify not available — skipping actor {actor_id}")
            return []

        timeout = timeout_secs or self.crawl_timeout
        max_memory_attempts = 4
        for attempt in range(1, max_memory_attempts + 1):
            with _ACTOR_LAUNCH_SEMAPHORE:
                try:
                    logger.info(f"Starting Apify actor: {actor_id}")
                    run = self._client.actor(actor_id).call(
                        run_input=run_input
                    )
                except Exception as e:
                    msg = str(e)
                    if "memory limit" in msg.lower():
                        wait_s = 30 * attempt
                        logger.warning(
                            f"Actor {actor_id} blocked by account memory limit "
                            f"(attempt {attempt}/{max_memory_attempts}) — sleeping {wait_s}s"
                        )
                        time.sleep(wait_s)
                        continue
                    logger.error(f"Actor {actor_id} failed: {e}")
                    return []

            if not run:
                logger.warning(f"Actor {actor_id} returned no run object")
                return []

            dataset_id = getattr(run, "default_dataset_id", None)
            if not dataset_id and hasattr(run, "get"):
                dataset_id = run.get("defaultDatasetId")
                
            if not dataset_id:
                logger.warning(f"Actor {actor_id} run completed but no dataset")
                return []

            try:
                items = list(self._client.dataset(dataset_id).iterate_items())
            except Exception as e:
                logger.error(f"Actor {actor_id} dataset fetch failed: {e}")
                return []
            logger.info(f"Actor {actor_id} returned {len(items)} items")
            return items

        logger.error(f"Actor {actor_id} still blocked after {max_memory_attempts} memory-limit attempts")
        return []

    def run_actors_batch(
        self,
        tasks: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Run multiple actor tasks in parallel (limited concurrency).
        Each task: {"name": str, "actor_id": str, "input": dict}
        Returns: {name: [items]}
        """
        results = {}
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            futures = {
                executor.submit(
                    self.run_actor,
                    task["actor_id"],
                    task["input"],
                ): task["name"]
                for task in tasks
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as e:
                    logger.error(f"Batch task '{name}' failed: {e}")
                    results[name] = []
        return results

    # ── Web Scraping via Apify ───────────────────────────────────────────────

    def scrape_urls(self, urls: List[str], page_function: str = None) -> List[Dict[str, Any]]:
        """
        Use Apify web-scraper to extract data from a list of URLs.
        Falls back to direct HTTP if Apify is not available.
        """
        if self.is_apify_available:
            return self._apify_scrape_urls(urls, page_function)
        return self._direct_scrape_urls(urls)

    def _apify_scrape_urls(self, urls: List[str], page_function: str = None) -> List[Dict[str, Any]]:
        """Scrape URLs via Apify web-scraper actor."""
        default_page_function = """
        async function pageFunction(context) {
            const { request, page, log } = context;
            let title = '';
            let text = '';
            try { title = await page.title() || ''; } catch(e) {}
            try {
                text = await page.evaluate(() => (document.body ? document.body.innerText : '')) || '';
            } catch(e) {}
            return {
                url: request.url,
                title: title,
                text: text.substring(0, 10000),
            };
        }
        """
        run_input = {
            "startUrls": [{"url": u} for u in urls],
            "pageFunction": page_function or default_page_function,
            "maxRequestsPerCrawl": len(urls),
            "maxConcurrency": min(len(urls), 5),
            "requestTimeoutSecs": 30,
        }
        return self.run_actor("apify/web-scraper", run_input)

    def _direct_scrape_urls(self, urls: List[str]) -> List[Dict[str, Any]]:
        """Direct HTTP scraping fallback — no Apify required."""
        results = []
        for url in urls:
            try:
                time.sleep(1.5)  # rate limiting
                resp = self._session.get(url, timeout=15, verify=False)
                if resp.status_code != 200:
                    logger.debug(f"HTTP {resp.status_code} for {url[:60]}")
                    continue
                soup = BeautifulSoup(resp.text, "lxml")
                for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                    tag.decompose()
                text = soup.get_text(separator=" ", strip=True)
                text = re.sub(r"\s{2,}", " ", text).strip()
                results.append({
                    "url": url,
                    "title": soup.title.string.strip() if soup.title and soup.title.string else "",
                    "text": text[:10000],
                })
            except Exception as e:
                logger.debug(f"Direct scrape failed for {url[:60]}: {e}")
        return results

    # ── RSS Feed Parsing ─────────────────────────────────────────────────────

    def fetch_rss_feed(self, feed_url: str) -> List[Dict[str, Any]]:
        """
        Fetch and parse an RSS/Atom feed. Returns list of items with
        title, link, published date, and description.
        """
        items = []
        try:
            resp = self._session.get(feed_url, timeout=15)
            if resp.status_code != 200:
                logger.debug(f"RSS feed returned {resp.status_code}: {feed_url[:60]}")
                return items

            soup = BeautifulSoup(resp.text, "xml")
            for entry in soup.find_all("item"):
                title_el = entry.find("title")
                link_el = entry.find("link")
                pub_date_el = entry.find("pubDate")
                desc_el = entry.find("description")

                if not title_el:
                    continue

                items.append({
                    "title": title_el.text.strip() if title_el else "",
                    "url": link_el.text.strip() if link_el else "",
                    "published": pub_date_el.text.strip() if pub_date_el else "",
                    "description": desc_el.text.strip()[:500] if desc_el else "",
                })

            logger.info(f"RSS {feed_url[:40]} returned {len(items)} items")
        except Exception as e:
            logger.debug(f"RSS feed failed {feed_url[:40]}: {e}")
        return items

    def fetch_rss_feeds_parallel(self, feed_urls: Dict[str, str]) -> Dict[str, List[Dict]]:
        """Fetch multiple RSS feeds in parallel. feed_urls: {name: url}"""
        results = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(self.fetch_rss_feed, url): name
                for name, url in feed_urls.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as e:
                    logger.debug(f"RSS parallel fetch '{name}' failed: {e}")
                    results[name] = []
        return results

    # ── Company Website Crawling ─────────────────────────────────────────────

    def crawl_company_pages(
        self,
        website: str,
        paths: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Crawl specific pages of a company website for team/contact/careers info.
        """
        if not paths:
            paths = ["/about", "/team", "/about-us", "/leadership",
                     "/founders", "/people", "/contact", "/careers"]

        base = website.rstrip("/")
        if not base.startswith("http"):
            base = f"https://{base}"

        urls = [f"{base}{p}" for p in paths]

        if self.is_apify_available:
            page_function = """
            async function pageFunction(context) {
                const { request, page, log } = context;
                const title = await page.title();
                const text = await page.evaluate(() => document.body.innerText);
                const links = await page.evaluate(() =>
                    Array.from(document.querySelectorAll('a[href]'))
                        .map(a => ({ text: a.innerText.trim(), href: a.href }))
                        .filter(l => l.href.includes('linkedin.com') ||
                                     l.href.includes('mailto:') ||
                                     l.text.length < 100)
                );
                const emails = text.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/g) || [];
                return {
                    url: request.url,
                    title: title,
                    text: text.substring(0, 8000),
                    links: links.slice(0, 50),
                    emails: [...new Set(emails)],
                };
            }
            """
            return self._apify_scrape_urls(urls, page_function)

        return self._direct_crawl_company_pages(urls)

    def _direct_crawl_company_pages(self, urls: List[str]) -> List[Dict[str, Any]]:
        """Direct HTTP crawl of company pages with email/link extraction."""
        results = []
        email_re = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

        for url in urls:
            try:
                time.sleep(1.0)
                resp = self._session.get(url, timeout=12, verify=False, allow_redirects=True)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "lxml")
                for tag in soup(["script", "style"]):
                    tag.decompose()

                text = soup.get_text(separator=" ", strip=True)
                text = re.sub(r"\s{2,}", " ", text).strip()

                # Extract links
                links = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "linkedin.com" in href or "mailto:" in href:
                        links.append({"text": a.get_text(strip=True)[:100], "href": href})

                # Extract emails
                emails = list(set(email_re.findall(text)))
                # Filter out common noise emails
                noise = {"example.com", "sentry.io", "wixpress.com", "w3.org", "schema.org"}
                emails = [e for e in emails if not any(n in e for n in noise)]

                results.append({
                    "url": url,
                    "title": soup.title.string.strip() if soup.title and soup.title.string else "",
                    "text": text[:8000],
                    "links": links[:50],
                    "emails": emails,
                })
            except Exception as e:
                logger.debug(f"Company page crawl failed {url[:60]}: {e}")
        return results

    # ── Startup India API ────────────────────────────────────────────────────

    def fetch_startup_india(self, page_size: int = 30) -> List[Dict[str, Any]]:
        """
        Query Startup India public REST API for recently funded startups.
        No auth required.
        """
        results = []
        endpoints = [
            {
                "url": "https://api.startupindia.gov.in/stic/api/noauth/discover/getStartupList",
                "method": "POST",
                "json": {
                    "pageNo": 0,
                    "pageSize": page_size,
                    "stages": ["FUNDED"],
                    "sectors": [],
                    "states": [],
                    "searchValue": "",
                },
            },
        ]

        for ep in endpoints:
            try:
                time.sleep(2)
                resp = getattr(self._session, ep["method"].lower())(
                    ep["url"],
                    headers={"Content-Type": "application/json"},
                    json=ep.get("json"),
                    timeout=15,
                )
                if resp.status_code != 200:
                    logger.debug(f"Startup India API returned {resp.status_code}")
                    continue

                data = resp.json()
                items = data.get("data") or data.get("startups") or data.get("content") or []

                for item in items[:page_size]:
                    name = (item.get("name") or item.get("startupName") or
                            item.get("entityName") or "").strip()
                    if not name:
                        continue

                    sector_raw = item.get("sectors") or item.get("sector") or []
                    industry = (
                        sector_raw[0] if isinstance(sector_raw, list) and sector_raw
                        else str(sector_raw) if sector_raw else "Technology"
                    )

                    results.append({
                        "company_name": name,
                        "website": item.get("website", ""),
                        "industry": industry,
                        "city": item.get("city") or item.get("district") or "India",
                        "state": item.get("state", ""),
                        "funding_stage": "Funded",
                        "funding_amount": "Undisclosed",
                        "investors": "Startup India",
                        "founding_year": str(item.get("incorporationYear", "")) or "",
                        "employee_count": "",
                        "company_description": (
                            item.get("shortDesc") or item.get("description") or ""
                        )[:300],
                        "source": "Startup India Gov API",
                        "source_url": "https://www.startupindia.gov.in",
                    })

                logger.info(f"Startup India API returned {len(results)} entries")
            except Exception as e:
                logger.warning(f"Startup India API failed: {e}")

        return results

    # ── Product Hunt ─────────────────────────────────────────────────────────

    def fetch_product_hunt_india(self, topics: List[str] = None) -> List[Dict[str, Any]]:
        """
        Scrape Product Hunt for Indian startup launches.
        Uses Apify if available, otherwise falls back to direct scraping.
        """
        topics = topics or ["india", "made-in-india"]
        results = []

        if self.is_apify_available:
            for topic in topics:
                url = f"https://www.producthunt.com/topics/{topic}"
                items = self.scrape_urls([url])
                for item in items:
                    parsed = self._parse_product_hunt_page(item.get("text", ""))
                    results.extend(parsed)
        else:
            for topic in topics:
                url = f"https://www.producthunt.com/topics/{topic}"
                try:
                    time.sleep(2)
                    resp = self._session.get(url, timeout=15)
                    if resp.status_code == 200:
                        parsed = self._parse_product_hunt_page(resp.text)
                        results.extend(parsed)
                except Exception as e:
                    logger.debug(f"Product Hunt scrape failed: {e}")

        logger.info(f"Product Hunt returned {len(results)} entries")
        return results

    def _parse_product_hunt_page(self, text: str) -> List[Dict[str, Any]]:
        """Extract product names from Product Hunt page text."""
        results = []
        # Product Hunt pages have structured product listings
        # We extract product name patterns from the text
        lines = text.split("\n") if "\n" in text else text.split(". ")
        seen = set()

        for line in lines:
            line = line.strip()
            if not line or len(line) < 5 or len(line) > 200:
                continue
            # Look for patterns like "ProductName — tagline" or "ProductName - tagline"
            m = re.match(r"^([A-Z][a-zA-Z0-9\s\.\-&]{1,40})\s*[—\-|]\s*(.+)", line)
            if m:
                name = m.group(1).strip()
                tagline = m.group(2).strip()
                if name.lower() not in seen and len(name) >= 2:
                    seen.add(name.lower())
                    results.append({
                        "company_name": name,
                        "website": "",
                        "industry": "Technology",
                        "city": "India",
                        "state": "",
                        "funding_stage": "Bootstrap",
                        "funding_amount": "Undisclosed",
                        "investors": "",
                        "founding_year": "",
                        "employee_count": "",
                        "company_description": tagline[:200],
                        "source": "Product Hunt",
                        "source_url": "https://www.producthunt.com",
                    })
        return results

    # ── Wellfound (AngelList) ────────────────────────────────────────────────

    def fetch_wellfound_india(self, base_url: str = None) -> List[Dict[str, Any]]:
        """
        Scrape Wellfound (AngelList Talent) for Indian startups.
        """
        url = base_url or "https://wellfound.com/startups/india"
        results = []

        pages = self.scrape_urls([url])
        for page in pages:
            text = page.get("text", "")
            parsed = self._parse_wellfound_page(text)
            results.extend(parsed)

        logger.info(f"Wellfound returned {len(results)} entries")
        return results

    def _parse_wellfound_page(self, text: str) -> List[Dict[str, Any]]:
        """Extract startup entries from Wellfound page text."""
        results = []
        # Wellfound pages list startups with: name, stage, size, location
        lines = text.split("\n") if "\n" in text else text.split(". ")
        seen = set()

        size_re = re.compile(r"(\d+[-–]\d+)\s*employees?", re.IGNORECASE)
        stage_re = re.compile(
            r"\b(Pre-Seed|Seed|Series [A-E]|Angel|Growth|Bootstrap)\b", re.IGNORECASE
        )

        for i, line in enumerate(lines):
            line = line.strip()
            if not line or len(line) < 3:
                continue

            # Check if line looks like a company name (short, capitalized)
            if 2 <= len(line) <= 40 and line[0].isupper() and line.lower() not in seen:
                # Look at surrounding context for stage/size info
                context = " ".join(lines[max(0, i-1):min(len(lines), i+4)])
                stage_m = stage_re.search(context)
                size_m = size_re.search(context)

                if stage_m or size_m or any(kw in context.lower() for kw in [
                    "startup", "funded", "hiring", "engineer", "product"
                ]):
                    seen.add(line.lower())
                    results.append({
                        "company_name": line,
                        "website": "",
                        "industry": "Technology",
                        "city": "India",
                        "state": "",
                        "funding_stage": stage_m.group(0).title() if stage_m else "Undisclosed",
                        "funding_amount": "Undisclosed",
                        "investors": "",
                        "founding_year": "",
                        "employee_count": size_m.group(1) if size_m else "",
                        "company_description": context[:200],
                        "source": "Wellfound",
                        "source_url": "https://wellfound.com/startups/india",
                    })
        return results

    # ── VC Portfolio Pages ───────────────────────────────────────────────────

    def fetch_vc_portfolios(self, portfolio_urls: List[str]) -> List[Dict[str, Any]]:
        """
        Scrape public VC portfolio pages to discover portfolio companies.
        """
        results = []
        pages = self.scrape_urls(portfolio_urls)

        for page in pages:
            url = page.get("url", "")
            text = page.get("text", "")
            vc_name = self._vc_name_from_url(url)

            parsed = self._parse_portfolio_page(text, vc_name, url)
            results.extend(parsed)

        logger.info(f"VC portfolios returned {len(results)} total entries")
        return results

    @staticmethod
    def _vc_name_from_url(url: str) -> str:
        """Extract VC fund name from portfolio URL."""
        domain = re.sub(r"^https?://(?:www\.)?", "", url).split("/")[0]
        name_map = {
            "purevc.fund": "PureVC",
            "100x.vc": "100X.VC",
            "stellarisvp.com": "Stellaris VP",
            "ivycap.in": "IvyCap Ventures",
            "blume.vc": "Blume Ventures",
            "kalaari.com": "Kalaari Capital",
            "chiratae.com": "Chiratae Ventures",
            "matrixpartners.in": "Matrix Partners India",
            "nexusvp.com": "Nexus VP",
            "lightspeedvp.com": "Lightspeed India",
        }
        return name_map.get(domain, domain)

    def _parse_portfolio_page(
        self, text: str, vc_name: str, source_url: str
    ) -> List[Dict[str, Any]]:
        """Extract company names from a VC portfolio page."""
        results = []
        seen = set()

        # Portfolio pages typically list company names as headings or links
        # Extract capitalized multi-word tokens that look like company names
        name_pattern = re.compile(
            r"\b([A-Z][a-zA-Z0-9]{1,25}(?:\s+[A-Z][a-zA-Z0-9]{1,25}){0,2})\b"
        )

        # Common non-company words to skip
        skip_words = {
            "Portfolio", "Companies", "Our", "Investments", "About", "Contact",
            "Team", "Blog", "News", "Home", "Partners", "Fund", "Venture",
            "Capital", "India", "Global", "Stage", "Sector", "Year",
            "Founded", "Headquarters", "Exit", "Exited", "Active", "All",
            "Series", "Seed", "Angel", "Pre", "Post", "Growth", "Early",
            "Late", "Bridge", "IPO", "Privacy", "Policy", "Terms", "Cookie",
            "Accept", "Close", "Menu", "Search", "Login", "Sign", "Join",
        }

        for match in name_pattern.finditer(text):
            name = match.group(1).strip()
            if (
                name.lower() not in seen
                and len(name) >= 2
                and name not in skip_words
                and not all(w in skip_words for w in name.split())
            ):
                seen.add(name.lower())
                results.append({
                    "company_name": name,
                    "website": "",
                    "industry": "Technology",
                    "city": "India",
                    "state": "",
                    "funding_stage": "VC-backed",
                    "funding_amount": "Undisclosed",
                    "investors": vc_name,
                    "founding_year": "",
                    "employee_count": "",
                    "company_description": f"Portfolio company of {vc_name}",
                    "source": f"VC Portfolio ({vc_name})",
                    "source_url": source_url,
                })

        return results

    # ── LinkedIn Search (via DuckDuckGo) ─────────────────────────────────────

    def linkedin_search(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """Search LinkedIn profiles using DuckDuckGo (free, no API key)."""
        try:
            from ddgs import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(
                    f"site:linkedin.com/in {query}", max_results=max_results
                ):
                    url = r.get("href", "")
                    if "linkedin.com/in/" not in url:
                        continue
                    results.append({
                        "name": r.get("title", "").split(" - ")[0].strip(),
                        "url": url,
                        "headline": r.get("body", ""),
                        "source": "LinkedIn (DDG)",
                    })
            logger.info(f"LinkedIn search returned {len(results)} profiles for: {query[:50]}")
            return results
        except Exception as e:
            logger.debug(f"LinkedIn search failed: {e}")
            return []

    # ── LinkedIn Jobs Frequency Discovery ────────────────────────────────────

    def fetch_linkedin_internships(
        self,
        keywords: str = "startup intern India",
        lookback_days: int = 14,
        min_postings: int = 1,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Discover early-stage startups by mining LinkedIn Jobs for internship postings.

        Strategy:
          1. Use Apify LinkedIn Jobs Scraper if available.
          2. Otherwise, run several DuckDuckGo queries targeting LinkedIn Jobs listings
             (e.g. "site:linkedin.com/jobs startup intern India").
          3. Parse company names, roles, and posting dates from the snippets.
          4. Group by company and count postings within `lookback_days`.
          5. Promote companies with >= `min_postings` as high-signal discovery candidates.

        Returns a list of startup-candidate dicts compatible with the discovery pipeline.
        """
        if self.is_apify_available:
            return self.fetch_linkedin_jobs_apify(keywords, lookback_days, max_results)

        import datetime as _dt

        cutoff = _dt.date.today() - _dt.timedelta(days=lookback_days)

        # Multiple search queries to maximise coverage
        queries = [
            f'site:linkedin.com/jobs {keywords}',
            f'site:linkedin.com/jobs "internship" "startup" India 2025',
            f'site:linkedin.com/jobs "project intern" OR "summer intern" India startup',
            f'site:linkedin.com/jobs "intern" "pre-seed" OR "seed stage" India',
        ]

        raw_hits: List[Dict] = []
        try:
            from ddgs import DDGS
            seen_urls: set = set()
            with DDGS() as ddgs:
                for q in queries:
                    time.sleep(3)  # be polite
                    try:
                        for r in ddgs.text(q, max_results=15):
                            url = r.get("href", "")
                            if url in seen_urls:
                                continue
                            seen_urls.add(url)
                            raw_hits.append(r)
                    except Exception as e:
                        logger.debug(f"DDG LinkedIn Jobs query failed: {e}")
                        time.sleep(5)
        except ImportError:
            logger.warning("ddgs not installed — LinkedIn Jobs source skipped. Run: pip install ddgs")
            return []

        logger.info(f"LinkedIn Jobs DDG: {len(raw_hits)} raw hits across {len(queries)} queries")

        # ── Parse company + date from each hit ───────────────────────────────
        # DDG snippets for LinkedIn Jobs typically look like:
        #   "Software Engineer Intern at Fintech Startup — Bengaluru · 3 days ago · ..."
        _DATE_CLUE_RE = re.compile(
            r"(\d+)\s*(day|week|month|hour)s?\s*ago", re.IGNORECASE
        )
        _COMPANY_RE = re.compile(
            r"(?:at|@)\s+([A-Z][a-zA-Z0-9&\.\-\s]{1,40}?)(?:\s*[·|—\-]|\s+\d|\s*$)",
        )

        company_hits: Dict[str, List[_dt.date]] = {}

        for hit in raw_hits:
            title   = hit.get("title", "")
            body    = hit.get("body",  "")
            combined = f"{title} {body}"

            # Derive approximate posting date from relative indicator
            date_m = _DATE_CLUE_RE.search(combined)
            if date_m:
                n, unit = int(date_m.group(1)), date_m.group(2).lower()
                delta = {
                    "hour":  _dt.timedelta(hours=n),
                    "day":   _dt.timedelta(days=n),
                    "week":  _dt.timedelta(weeks=n),
                    "month": _dt.timedelta(days=n * 30),
                }.get(unit, _dt.timedelta(days=n))
                post_date = _dt.date.today() - delta
            else:
                post_date = _dt.date.today()   # assume recent if no clue

            # Skip if outside lookback window
            if post_date < cutoff:
                continue

            # Extract company name
            company_m = _COMPANY_RE.search(combined)
            if not company_m:
                # Fallback: try extracting from URL  linkedin.com/jobs/view/.../company-name
                url = hit.get("href", "")
                parts = [p for p in url.split("/") if p and not p.isdigit()]
                company_raw = parts[-1].replace("-", " ").title() if parts else ""
            else:
                company_raw = company_m.group(1).strip()

            if not company_raw or len(company_raw) < 2:
                continue

            # Normalise name (strip legal suffixes)
            company_raw = re.sub(
                r"(?i)\s+(pvt|private|ltd|limited|inc|corp|llc|co)\.?$",
                "", company_raw
            ).strip()

            company_hits.setdefault(company_raw, []).append(post_date)

        # ── Build candidate list from frequency analysis ──────────────────────
        candidates: List[Dict[str, Any]] = []
        for company, dates in company_hits.items():
            count = len(dates)
            if count < min_postings:
                continue

            # Compute average gap between postings (days); fewer gap = more active
            if count >= 2:
                sorted_dates = sorted(dates)
                gaps = [(sorted_dates[i+1] - sorted_dates[i]).days for i in range(len(sorted_dates)-1)]
                avg_gap = sum(gaps) / len(gaps)
            else:
                avg_gap = lookback_days  # single posting — full window

            candidates.append({
                "company_name":       company,
                "website":            "",
                "industry":           "Technology",
                "city":               "India",
                "state":              "",
                "funding_stage":      "Undisclosed",
                "funding_amount":     "Undisclosed",
                "investors":          "",
                "founding_year":      "",
                "employee_count":     "",
                "company_description": (
                    f"Actively hiring interns — {count} posting(s) in last "
                    f"{lookback_days} days (avg gap: {avg_gap:.0f}d)."
                ),
                "source":             "LinkedIn Jobs (Frequency)",
                "source_url":         "https://www.linkedin.com/jobs/",
                # Internal scoring hint for the AI scorer
                "_linkedin_posting_count": count,
                "_linkedin_avg_gap_days":  round(avg_gap, 1),
            })

        # Sort: most active (most postings, smallest gap) first
        candidates.sort(key=lambda x: (-x["_linkedin_posting_count"], x["_linkedin_avg_gap_days"]))
        candidates = candidates[:max_results]

        logger.info(
            f"LinkedIn Jobs frequency: {len(candidates)} unique companies pass threshold "
            f"(min_postings={min_postings}, lookback={lookback_days}d)"
        )
        return candidates

    def fetch_linkedin_jobs_apify(
        self,
        keywords: str = "startup intern India",
        lookback_days: int = 14,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Discover early-stage startups using Apify's LinkedIn Jobs Scraper actor.
        """
        from src.config import APIFY_LINKEDIN_JOBS_ACTOR
        run_input = {
            "keywords": keywords,
            "location": "India",
            "datePosted": "pastMonth",  # closest option for 14 days
            "limitPerSource": min(max_results, 100),
        }
        
        items = self.run_actor(APIFY_LINKEDIN_JOBS_ACTOR, run_input)
        candidates = []
        seen = set()
        
        for item in items:
            company = item.get("companyName")
            if not company or company.lower() in seen:
                continue
                
            seen.add(company.lower())
            candidates.append({
                "company_name": company,
                "website": "",
                "industry": "Technology",
                "city": item.get("location", "India"),
                "state": "",
                "funding_stage": "Undisclosed",
                "funding_amount": "Undisclosed",
                "investors": "",
                "founding_year": "",
                "employee_count": "",
                "company_description": f"Hiring: {item.get('title', '')} ({item.get('postedAt', '')})",
                "source": "LinkedIn Jobs (Apify)",
                "source_url": item.get("link") or item.get("url", "https://www.linkedin.com/jobs/"),
                "_linkedin_posting_count": 1,
                "_linkedin_avg_gap_days": 1.0,
            })
            
        logger.info(f"Apify LinkedIn Jobs returned {len(candidates)} unique companies")
        return candidates

    # ── Job Platform Frequency Discovery (Instahyre, Cutshort, Internshala, Unstop, Indeed) ──

    def fetch_job_platform_frequency(
        self,
        platforms: Dict[str, str] = None,
        platform_urls: Dict[str, str] = None,
        keywords: str = "startup intern India hiring",
        lookback_days: int = 21,
        min_postings: int = 1,
        max_results: int = 60,
    ) -> List[Dict[str, Any]]:
        """
        Discover startups by mining Indian job platforms for job postings.

        Strategy:
          1. For each platform, run site: queries on DuckDuckGo targeting
             `site:<domain> <keywords>` (plus a few variants).
          2. Optionally scrape the platform's search page text directly.
          3. Parse company names from snippets/page text and estimate the
             posting date from relative indicators ("3 days ago", "1 week ago").
          4. Group by company and count postings within `lookback_days`.
          5. Companies posting FREQUENTLY (>= `min_postings`) are flagged —
             active hiring sprees are a strong recent-funding signal.

        Returns startup-candidate dicts compatible with the discovery pipeline.
        """
        import datetime as _dt
        from src.config import (
            JOB_PLATFORM_SITES, JOB_PLATFORM_URLS,
            JOB_PLATFORM_KEYWORDS, JOB_PLATFORM_LOOKBACK_DAYS,
            JOB_PLATFORM_MIN_POSTINGS, JOB_PLATFORM_MAX_RESULTS,
        )

        sites = platforms or JOB_PLATFORM_SITES
        urls_by_name = platform_urls or JOB_PLATFORM_URLS
        keywords = keywords or JOB_PLATFORM_KEYWORDS
        lookback_days = lookback_days or JOB_PLATFORM_LOOKBACK_DAYS
        min_postings = min_postings or JOB_PLATFORM_MIN_POSTINGS
        max_results = max_results or JOB_PLATFORM_MAX_RESULTS

        cutoff = _dt.date.today() - _dt.timedelta(days=lookback_days)

        # ── 1. Collect raw hits per platform ────────────────────────────────
        raw_hits: List[Dict] = []
        try:
            from ddgs import DDGS
            seen_urls: set = set()
            with DDGS() as ddgs:
                for platform, domain in sites.items():
                    queries = [
                        f'site:{domain} {keywords}',
                        f'site:{domain} "intern" OR "graduate" OR "engineer" startup India',
                        f'site:{domain} "hiring" "startup" India 2025 OR 2026',
                    ]
                    for q in queries:
                        time.sleep(2)  # be polite
                        try:
                            for r in ddgs.text(q, max_results=15):
                                url = r.get("href", "")
                                if url in seen_urls:
                                    continue
                                seen_urls.add(url)
                                r["_platform"] = platform
                                r["_domain"] = domain
                                raw_hits.append(r)
                        except Exception as e:
                            logger.debug(f"DDG job-platform query failed ({platform}): {e}")
                            time.sleep(4)
        except ImportError:
            logger.warning("ddgs not installed — job platform source skipped. Run: pip install ddgs")
            return []

        # ── 2. Scrape platform search pages for additional company names ────
        page_texts: List[str] = []
        page_urls = [u for n, u in urls_by_name.items() if u]
        if page_urls:
            pages = self.scrape_urls(page_urls[:3])
            for pg in pages:
                text = pg.get("text", "")
                if text:
                    page_texts.append(text)

        logger.info(f"Job platforms: {len(raw_hits)} DDG hits + {len(page_texts)} scraped pages")

        # ── 3. Parse company + date from each hit ───────────────────────────
        _DATE_CLUE_RE = re.compile(
            r"(\d+)\s*(day|week|month|hour)s?\s*ago", re.IGNORECASE
        )
        _COMPANY_RE = re.compile(
            r"(?:at|@)\s+([A-Z][a-zA-Z0-9&\.\-\s]{1,40}?)(?:\s*[·|—\-]|\s+\d|\s*$)",
        )
        # Pick "At <Company>" out of long titles: "...Internship At Tailored AI Bangalore"
        _AT_SUFFIX_RE = re.compile(
            r"(?:intern|internship|job|position|opening|role)s?\s+(?:at|@)\s+"
            r"([A-Z][a-zA-Z0-9&\.\-\s]{1,40}?)(?:\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s*)?$"
        )
        # Generic landing-page words that are never real company names
        _NOISE_RE = re.compile(
            r"\b(jobs?|interns?|internship|hiring|hire|hired|salary|pay|scholarship|fellowship|"
            r"opportunit|position|opening|role|category|search|results?|index|profile|login|"
            r"sign|register|browse|latest|best|top|highest|paying|remote|hybrid|work.?from.?home|"
            r"how\s+to|become|developer|engineer|manager|analyst|designer|consultant|marketing|"
            r"company|companies|blog|product|account|executive|associate|"
            r"in\s+(india|bangalore|bengaluru|hyderabad|delhi|noida|gurgaon|gurugram|pune|mumbai|"
            r"chennai|kolkata|anywhere))\b",
            re.IGNORECASE,
        )
        _HTML_NOISE_RE = re.compile(r"(\.s?html|www\.|\.(?:com|co\.in|io|in|ai|net|org|tech)\b|&amp;|&#\d+|\?)", re.IGNORECASE)

        company_hits: Dict[str, Dict[str, Any]] = {}

        def _register_company(company_raw: str, post_date: _dt.date, platform: str):
            if not company_raw or len(company_raw) < 2:
                return
            company_raw = re.sub(
                r"(?i)\s+(pvt|private|ltd|limited|inc|corp|llc|co|technologies|solutions|services|ventures|startups?|multi|online|digital|web)\.?$",
                "", company_raw
            ).strip()
            # Cut trailing city/state names: "Tailored Ai Bangalore" -> "Tailored Ai"
            company_raw = re.sub(
                r"(?i)\s+(bangalore|bengaluru|hyderabad|delhi\s*ncr|ncr|new\s+delhi|noida|"
                r"gurgaon|gurugram|pune|mumbai|chennai|kolkata|ahmedabad|jaipur|india)\s*$",
                "", company_raw
            ).strip()
            company_raw = company_raw.rstrip(".")
            if len(company_raw) < 2 or len(company_raw) > 40:
                return
            # Skip names containing session-ID style tokens (mixed alnum >= 10 chars)
            if re.search(r"\b[A-Za-z]*[0-9][A-Za-z0-9]{9,}\b", company_raw):
                return
            if _NOISE_RE.search(company_raw) or _HTML_NOISE_RE.search(company_raw):
                return
            if any(w in company_raw.lower() for w in ("jobs", "internship", "interns", "hiring")):
                return
            entry = company_hits.setdefault(company_raw, {"dates": [], "platforms": set()})
            entry["dates"].append(post_date)
            entry["platforms"].add(platform)

        for hit in raw_hits:
            title   = hit.get("title", "")
            body    = hit.get("body",  "")
            platform = hit.get("_platform", "Job Platform")
            combined = f"{title} {body}"

            date_m = _DATE_CLUE_RE.search(combined)
            if date_m:
                n, unit = int(date_m.group(1)), date_m.group(2).lower()
                delta = {
                    "hour":  _dt.timedelta(hours=n),
                    "day":   _dt.timedelta(days=n),
                    "week":  _dt.timedelta(weeks=n),
                    "month": _dt.timedelta(days=n * 30),
                }.get(unit, _dt.timedelta(days=n))
                post_date = _dt.date.today() - delta
            else:
                post_date = _dt.date.today()

            if post_date < cutoff:
                continue

            company_m = _COMPANY_RE.search(combined)
            if company_m:
                company_raw = company_m.group(1).strip()
            else:
                at_m = _AT_SUFFIX_RE.search(title)
                company_raw = at_m.group(1).strip() if at_m else ""
                if not company_raw:
                    url = hit.get("href", "")
                    parts = [p for p in url.split("/") if p and not p.isdigit()]
                    company_raw = parts[-1].replace("-", " ").title() if parts else ""

            _register_company(company_raw, post_date, platform)

        # Also mine scraped page text for "Company — Role" style listings
        for text in page_texts:
            for m in re.finditer(
                r"([A-Z][a-zA-Z0-9&\.\-\s]{1,40}?)\s*[—\-|]\s*[A-Z][a-zA-Z0-9\s]{3,60}", text
            ):
                name = m.group(1).strip()
                if len(name) < 2:
                    continue
                _register_company(name, _dt.date.today(), "Job Platform Page")

        # ── 4. Build candidates from frequency analysis ─────────────────────
        candidates: List[Dict[str, Any]] = []
        for company, entry in company_hits.items():
            dates = sorted(entry["dates"])
            count = len(dates)
            if count < min_postings:
                continue

            if count >= 2:
                gaps = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
                avg_gap = sum(gaps) / len(gaps)
            else:
                avg_gap = lookback_days

            platforms_src = ", ".join(sorted(entry["platforms"]))

            candidates.append({
                "company_name": company,
                "website": "",
                "industry": "Technology",
                "city": "India",
                "state": "",
                "funding_stage": "Undisclosed",
                "funding_amount": "Undisclosed",
                "investors": "",
                "founding_year": "",
                "employee_count": "",
                "company_description": (
                    f"Actively hiring on {platforms_src} — {count} posting(s) in last "
                    f"{lookback_days} days (avg gap: {avg_gap:.0f}d). "
                    f"Frequent postings suggest a post-funding hiring spree."
                ),
                "source": f"Job Platforms ({platforms_src})",
                "source_url": urls_by_name.get(list(entry["platforms"])[0], "https://www.instahyre.com"),
                # Internal scoring hints for the AI scorer / priority engine
                "_job_posting_count": count,
                "_job_avg_gap_days":  round(avg_gap, 1),
                "_hiring_frequency_signal": True,
            })

        # Most active first
        candidates.sort(key=lambda x: (-x["_job_posting_count"], x["_job_avg_gap_days"]))
        candidates = candidates[:max_results]

        logger.info(
            f"Job platform frequency: {len(candidates)} unique companies pass threshold "
            f"(min_postings={min_postings}, lookback={lookback_days}d)"
        )
        return candidates

    # ── LinkedIn Profile Search & Scrape (Apify Enablers) ────────────────────
    def search_linkedin_profiles(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        Search for LinkedIn profiles by filters using harvestapi/linkedin-profile-search.
        """
        if not self.is_apify_available:
            return self.linkedin_search(query, max_results)
            
        from src.config import APIFY_LINKEDIN_PROFILE_SEARCH_ACTOR
        run_input = {
            "searchQuery": query,
            "profileScraperMode": "Short",
            "maxItems": max_results,
            "takePages": max(1, (max_results + 24) // 25),
        }
        
        items = self.run_actor(APIFY_LINKEDIN_PROFILE_SEARCH_ACTOR, run_input)
        results = []
        for item in items:
            name = (
                item.get("fullName")
                or f"{item.get('firstName', '')} {item.get('lastName', '')}".strip()
            )
            results.append({
                "name": name,
                "url": item.get("profileUrl") or item.get("linkedinUrl", ""),
                "headline": item.get("headline", ""),
                "source": "LinkedIn Search (Apify)",
            })
        return results

    def scrape_linkedin_profiles(self, urls: List[str]) -> List[Dict[str, Any]]:
        """
        Extract detailed profile data (work, education, emails) from LinkedIn URLs
        using harvestapi/linkedin-profile-scraper.
        """
        if not self.is_apify_available or not urls:
            return []
            
        from src.config import APIFY_LINKEDIN_PROFILE_SCRAPER_ACTOR
        run_input = {
            "profileScraperMode": "Profile details no email ($4 per 1k)",
            "urls": urls,
        }
        
        items = self.run_actor(APIFY_LINKEDIN_PROFILE_SCRAPER_ACTOR, run_input, timeout_secs=self.crawl_timeout * 2)
        return items

    # ── Facebook Ad Library Scraper ──────────────────────────────────────────

    def fetch_facebook_ads(self, search_terms: List[str], country: str = "IN", max_results: int = 50) -> List[Dict[str, Any]]:
        """
        Discover startups running Facebook/Meta ads using curious_coder/facebook-ads-library-scraper.
        Signals active marketing budget.
        """
        if not self.is_apify_available:
            logger.warning("Apify not available - skipping Facebook Ads discovery")
            return []
            
        from src.config import APIFY_FB_ADS_ACTOR
        candidates = []
        seen = set()
        
        for term in search_terms:
            urls = [
                {
                    "url": (
                        "https://www.facebook.com/ads/library/"
                        f"?active_status=active&ad_type=all&country={country}&q={term.replace(' ', '+')}"
                    )
                }
            ]
            run_input = {
                "urls": urls,
                "count": max_results,
                "scrapeAdDetails": False,
            }
            
            items = self.run_actor(APIFY_FB_ADS_ACTOR, run_input)
            for item in items:
                page_name = item.get("pageName") or item.get("advertiserName") or ""
                if not page_name or page_name.lower() in seen:
                    continue
                    
                seen.add(page_name.lower())
                candidates.append({
                    "company_name": page_name,
                    "website": item.get("pageProfileUri") or item.get("pageUrl") or "",
                    "industry": "Technology",
                    "city": "India",
                    "state": "",
                    "funding_stage": "Funded",
                    "funding_amount": "Undisclosed",
                    "investors": "",
                    "founding_year": "",
                    "employee_count": "",
                    "company_description": f"Running Meta Ads (Search: {term})",
                    "source": "Facebook Ads (Apify)",
                    "source_url": item.get("adArchiveUrl") or item.get("adUrl") or "",
                })
                
        logger.info(f"Apify Facebook Ads returned {len(candidates)} unique companies")
        return candidates

    # ── Y Combinator (via public Algolia search index) ───────────────────────

    YC_ALGOLIA_INDEX = "YCCompany_production"
    YC_DIRECTORY_URL = "https://www.ycombinator.com/companies/"
    # Algolia App ID is public and embedded in YC's frontend bundle.
    YC_ALGOLIA_APP = "45BWZJ1SGC"

    def _get_yc_algolia_creds(self) -> Dict[str, str]:
        """
        Extract the current Algolia public search key from YC's companies page.
        The key rotates on redeploys, so we pull it fresh instead of hardcoding.
        """
        try:
            resp = self._session.get(self.YC_DIRECTORY_URL, timeout=20)
            if resp.status_code == 200:
                m = re.search(
                    r"window\.AlgoliaOpts\s*=\s*({.*?});", resp.text, re.DOTALL
                )
                if m:
                    opts = json.loads(m.group(1))
                    if opts.get("app") and opts.get("key"):
                        return {"app": opts["app"], "key": opts["key"]}
        except Exception as e:
            logger.debug(f"YC Algolia creds extraction failed: {e}")
        return {}

    def fetch_y_combinator(
        self,
        recent_batches: List[str] = None,
        regions: List[str] = None,
        max_results: int = 300,
    ) -> List[Dict[str, Any]]:
        """
        Discover startups from Y Combinator's public Algolia directory index.
        Targets recent batches (configurable) and can restrict to regions
        (e.g. India / South Asia) to match the Indian-startup focus.

        No Apify actor is required - the search-only Algolia index is public.
        """
        if not recent_batches:
            recent_batches = [
                "Fall 2026", "Summer 2026", "Spring 2026", "Winter 2026",
                "Fall 2025", "Summer 2025", "Spring 2025", "Winter 2025",
            ]

        creds = self._get_yc_algolia_creds()
        if not creds:
            logger.warning(
                "Could not extract YC Algolia credentials - Y Combinator source skipped."
            )
            return []

        url = (
            f"https://{creds['app'].lower()}-dsn.algolia.net/1/indexes/"
            f"{self.YC_ALGOLIA_INDEX}/query"
        )
        headers = {
            "X-Algolia-Application-Id": creds["app"],
            "X-Algolia-API-Key": creds["key"],
            "Content-Type": "application/json",
        }

        facet_filters = []
        batch_filter = [f"batch:{b}" for b in recent_batches]
        facet_filters.append(batch_filter)
        if regions:
            region_filter = [f"regions:{r}" for r in regions]
            facet_filters.append(region_filter)

        payload = {
            "query": "",
            "hitsPerPage": min(max_results, 1000),
            "facetFilters": facet_filters,
        }

        results = []
        try:
            resp = self._session.post(url, headers=headers, json=payload, timeout=25)
            if resp.status_code != 200:
                logger.warning(f"YC Algolia query returned HTTP {resp.status_code}")
                return []
            data = resp.json()
            hits = data.get("hits", [])
            for h in hits:
                candidate = self._yc_hit_to_candidate(h)
                if candidate:
                    results.append(candidate)
            logger.info(f"Y Combinator returned {len(results)} entries")
        except Exception as e:
            logger.warning(f"YC Algolia query failed: {e}")

        return results[:max_results]

    @staticmethod
    def _yc_hit_to_candidate(h: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Map an Algolia YC hit to the discovery candidate shape."""
        name = (h.get("name") or "").strip()
        if not name or len(name) < 2:
            return None

        status = (h.get("status") or "").lower()
        # Skip defunct companies (we only want active / investable ones)
        if status in ("inactive", "acquired", "public"):
            return None

        # Map YC stage -> early-stage funding stage used by the pipeline filter
        stage_map = {
            "early": "Seed",
            "growth": "Series A",
            "other": "Undisclosed",
        }
        yc_stage = (h.get("stage") or "").lower()
        funding_stage = stage_map.get(yc_stage, "Undisclosed")

        industries = h.get("industries") or []
        industry = (
            h.get("industry")
            or (industries[0] if industries else "Technology")
        )

        # Derive founding year from the batch (e.g. "Winter 2026" -> 2026)
        batch = h.get("batch", "")
        year_m = re.search(r"(20\d{2})", batch)
        founding_year = year_m.group(1) if year_m else ""

        location = h.get("all_locations", "") or ""
        city = location.split(",")[0].strip() if location else "India"

        team_size = h.get("team_size")
        employee_count = str(team_size) if isinstance(team_size, int) else ""

        one_liner = h.get("one_liner") or ""
        long_desc = h.get("long_description") or ""
        description = (one_liner + (" " + long_desc if long_desc else "")).strip()

        return {
            "company_name": name,
            "website": h.get("website", ""),
            "industry": industry,
            "city": city,
            "state": "",
            "funding_stage": funding_stage,
            "funding_amount": "Undisclosed",
            "investors": "Y Combinator",
            "founding_year": founding_year,
            "employee_count": employee_count,
            "company_description": description[:300],
            "source": "Y Combinator",
            "source_url": f"https://www.ycombinator.com/companies/{h.get('slug', '')}",
            "yc_batch": batch,
            "yc_slug": h.get("slug", ""),
            "yc_regions": ", ".join(h.get("regions", []) or []),
            "yc_status": h.get("status", ""),
            "yc_team_size": team_size,
        }

    # ── Utility: Jina AI Reader (free article extraction) ────────────────────

    def extract_article_text(self, url: str) -> Optional[str]:
        """
        Extract article text using Jina AI Reader API (free).
        Falls back to direct scraping.
        """
        # Try Jina first
        try:
            jina_url = f"https://r.jina.ai/{url}"
            resp = self._session.get(jina_url, timeout=20)
            if resp.status_code == 200 and len(resp.text) > 100:
                logger.debug(f"Jina extracted {len(resp.text)} chars from {url[:50]}")
                return resp.text.strip()
        except Exception as e:
            logger.debug(f"Jina failed for {url[:50]}: {e}")

        # Direct scrape fallback
        try:
            resp = self._session.get(url, timeout=15, verify=False)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                text = soup.get_text(separator=" ", strip=True)
                return re.sub(r"\s{2,}", " ", text)[:10000]
        except Exception as e:
            logger.debug(f"Direct article extract failed for {url[:50]}: {e}")

        return None
