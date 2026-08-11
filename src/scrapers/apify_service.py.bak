"""
Web scraping service using free tools:
  - Jina AI Reader API for article extraction (https://r.jina.ai/{url})
  - Direct HTTP + BeautifulSoup as fallback
  - Bing HTML scraping for search

No API keys required. All free.
"""
import os
import time
import logging
import requests
import re
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup

logger = logging.getLogger("ScrapingService")


class ScrapingService:
    """
    Unified web scraping service.
    Uses Jina AI Reader API (free) + direct HTTP scraping + Bing search.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.session.verify = False

    def is_available(self) -> bool:
        return True

    def extract_article(self, url: str) -> Optional[str]:
        """
        Extract article text using Jina AI Reader API.
        Falls back to direct HTTP scraping if Jina fails.
        """
        text = self._jina_extract(url)
        if not text:
            text = self._direct_scrape(url)
        return text

    def _jina_extract(self, url: str) -> Optional[str]:
        """
        Use Jina AI Reader API to extract article content.
        Free API: https://r.jina.ai/{url}
        """
        try:
            api_url = f"https://r.jina.ai/{url}"
            resp = self.session.get(api_url, timeout=20)
            if resp.status_code == 200:
                text = resp.text.strip()
                if len(text) > 100:
                    logger.info(f"Jina extracted {len(text)} chars from {url[:60]}")
                    return text
            else:
                logger.debug(f"Jina returned {resp.status_code} for {url[:60]}")
        except Exception as e:
            logger.debug(f"Jina extraction failed: {e}")
        return None

    def _direct_scrape(self, url: str) -> Optional[str]:
        """
        Direct HTTP scraping with BeautifulSoup as fallback.
        Uses safe_get from utils for better anti-bot handling.
        """
        try:
            from src.scrapers.utils import safe_get, extract_text
            resp = safe_get(url, timeout=15)
            if resp and resp.status_code == 200:
                text = extract_text(resp.text, max_chars=10000)
                if len(text) > 100:
                    logger.info(f"Direct scrape {len(text)} chars from {url[:60]}")
                    return text
        except Exception as e:
            logger.debug(f"Direct scrape failed: {e}")
        return None

    def scrape_inc42(self) -> List[Dict[str, Any]]:
        """Scrape Inc42 funding news using direct HTTP."""
        results = []
        urls = [
            "https://inc42.com/tag/funding/",
            "https://inc42.com/tag/funding-alert/",
            "https://inc42.com/buzz/",
            "https://inc42.com/startups/",
        ]
        for feed_url in urls:
            text = self._safe_extract(feed_url)
            if text:
                results.extend(self._parse_funding_page(text, feed_url, "Inc42"))
        return results

    def scrape_entrackr(self) -> List[Dict[str, Any]]:
        """Scrape Entrackr news using direct HTTP."""
        results = []
        urls = [
            "https://entrackr.com/category/news/",
            "https://entrackr.com/",
            "https://entrackr.com/category/funding/",
        ]
        for feed_url in urls:
            text = self._safe_extract(feed_url)
            if text:
                results.extend(self._parse_funding_page(text, feed_url, "Entrackr"))
        return results

    def scrape_yourstory(self) -> List[Dict[str, Any]]:
        """Scrape YourStory funding news using direct HTTP."""
        results = []
        urls = [
            "https://yourstory.com/tag/funding",
            "https://yourstory.com/tag/startup-funding",
            "https://yourstory.com/section/enterprise",
        ]
        for feed_url in urls:
            text = self._safe_extract(feed_url)
            if text:
                results.extend(self._parse_funding_page(text, feed_url, "YourStory"))
        return results

    def scrape_vccircle(self) -> List[Dict[str, Any]]:
        """Scrape VCCircle funding news."""
        results = []
        urls = [
            "https://www.vccircle.com/deals/",
            "https://www.vccircle.com/",
        ]
        for feed_url in urls:
            text = self._safe_extract(feed_url)
            if text:
                results.extend(self._parse_funding_page(text, feed_url, "VCCircle"))
        return results

    def scrape_business_today(self) -> List[Dict[str, Any]]:
        """Scrape Business Today startup funding news."""
        results = []
        urls = [
            "https://www.businesstoday.in/startup",
            "https://www.businesstoday.in/startup/article/startup-funding",
        ]
        for feed_url in urls:
            text = self._safe_extract(feed_url)
            if text:
                results.extend(self._parse_funding_page(text, feed_url, "Business Today"))
        return results

    def scrape_economic_times(self) -> List[Dict[str, Any]]:
        """Scrape Economic Times startup funding news."""
        results = []
        urls = [
            "https://economictimes.indiatimes.com/tech-startups",
            "https://economictimes.indiatimes.com/tech-startups/startup-funding",
        ]
        for feed_url in urls:
            text = self._safe_extract(feed_url)
            if text:
                results.extend(self._parse_funding_page(text, feed_url, "Economic Times"))
        return results

    def scrape_yourstory_rss(self) -> List[Dict[str, Any]]:
        """Scrape YourStory RSS feed."""
        results = []
        try:
            url = "https://yourstory.com/feed"
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "xml")
                items = soup.find_all("item")
                for item in items:
                    title = item.find("title")
                    link = item.find("link")
                    if not title or not link:
                        continue
                    title_text = title.text.strip()
                    if not any(kw in title_text.lower() for kw in ["fund", "raise", "invest", "series", "seed", "crore", "million"]):
                        continue
                    company = self._extract_company_from_news_title(title_text)
                    if not company:
                        continue
                    results.append({
                        "title": title_text,
                        "url": link.text.strip(),
                        "snippet": title_text,
                        "source": "YourStory RSS",
                        "date": "",
                        "company_name": company,
                        "funding_amount": self._extract_amount_from_text(title_text),
                        "funding_stage": self._extract_stage_from_text(title_text),
                    })
                logger.info(f"YourStory RSS returned {len(results)} articles")
        except Exception as e:
            logger.debug(f"YourStory RSS failed: {e}")
        return results

    def scrape_entrackr_rss(self) -> List[Dict[str, Any]]:
        """Scrape Entrackr RSS feed."""
        results = []
        try:
            url = "https://entrackr.com/feed/"
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "xml")
                items = soup.find_all("item")
                for item in items:
                    title = item.find("title")
                    link = item.find("link")
                    if not title or not link:
                        continue
                    title_text = title.text.strip()
                    if not any(kw in title_text.lower() for kw in ["fund", "raise", "invest", "series", "seed", "crore", "million"]):
                        continue
                    company = self._extract_company_from_news_title(title_text)
                    if not company:
                        continue
                    results.append({
                        "title": title_text,
                        "url": link.text.strip(),
                        "snippet": title_text,
                        "source": "Entrackr RSS",
                        "date": "",
                        "company_name": company,
                        "funding_amount": self._extract_amount_from_text(title_text),
                        "funding_stage": self._extract_stage_from_text(title_text),
                    })
                logger.info(f"Entrackr RSS returned {len(results)} articles")
        except Exception as e:
            logger.debug(f"Entrackr RSS failed: {e}")
        return results

    def scrape_inc42_rss(self) -> List[Dict[str, Any]]:
        """Scrape Inc42 RSS feed."""
        results = []
        try:
            url = "https://inc42.com/feed/"
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "xml")
                items = soup.find_all("item")
                for item in items:
                    title = item.find("title")
                    link = item.find("link")
                    if not title or not link:
                        continue
                    title_text = title.text.strip()
                    if not any(kw in title_text.lower() for kw in ["fund", "raise", "invest", "series", "seed", "crore", "million"]):
                        continue
                    company = self._extract_company_from_news_title(title_text)
                    if not company:
                        continue
                    results.append({
                        "title": title_text,
                        "url": link.text.strip(),
                        "snippet": title_text,
                        "source": "Inc42 RSS",
                        "date": "",
                        "company_name": company,
                        "funding_amount": self._extract_amount_from_text(title_text),
                        "funding_stage": self._extract_stage_from_text(title_text),
                    })
                logger.info(f"Inc42 RSS returned {len(results)} articles")
        except Exception as e:
            logger.debug(f"Inc42 RSS failed: {e}")
        return results

    def _safe_extract(self, url: str) -> Optional[str]:
        """
        Extract text from URL using Jina AI Reader + direct HTTP fallback.
        """
        text = self._jina_extract(url)
        if not text:
            text = self._direct_scrape(url)
        return text

    def scrape_google_news(self, queries: List[str] = None, max_results: int = 30) -> List[Dict[str, Any]]:
        """
        Scrape Google News RSS for startup funding articles.
        Uses parallel fetching for speed.
        Returns list of {title, url, snippet, source, date, company_name, funding_amount, funding_stage}
        """
        import concurrent.futures
        
        results = []
        queries = queries or [
            "Indian startup funding 2025",
            "Indian startup funding 2026",
            "Indian startup raised funding 2025",
            "Indian startup Series A seed 2025",
            "Indian startup unicorn funding 2026",
            "Bengaluru startup funding 2026",
            "Mumbai startup funding 2026",
            "Delhi startup funding 2026",
        ]
        seen_urls = set()
        
        def fetch_query(query: str) -> List[Dict[str, Any]]:
            query_results = []
            try:
                url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
                resp = self.session.get(url, timeout=20)
                if resp.status_code != 200:
                    return []
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "xml")
                items = soup.find_all("item")
                for item in items[:max_results]:
                    title = item.find("title")
                    link = item.find("link")
                    pub_date = item.find("pubDate")
                    title_text = title.text.strip() if title else ""
                    link_text = link.text.strip() if link else ""
                    if not title_text or not link_text or link_text in seen_urls:
                        continue
                    seen_urls.add(link_text)
                    company = self._extract_company_from_news_title(title_text)
                    amount = self._extract_amount_from_text(f"{title_text}")
                    stage = self._extract_stage_from_text(f"{title_text}")
                    query_results.append({
                        "title": title_text,
                        "url": link_text,
                        "snippet": title_text,
                        "source": "Google News",
                        "date": pub_date.text.strip() if pub_date else "",
                        "company_name": company,
                        "funding_amount": amount,
                        "funding_stage": stage,
                    })
                if items:
                    logger.info(f"Google News RSS returned {len(items)} articles for: {query[:50]}")
            except Exception as e:
                logger.debug(f"Google News RSS failed for {query[:50]}: {e}")
            return query_results
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(fetch_query, q) for q in queries]
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.extend(future.result())
                except Exception as e:
                    logger.debug(f"Parallel fetch error: {e}")
        
        return results

    def _extract_company_from_news_title(self, title: str) -> str:
        """Extract company name from news title."""
        # Skip generic headlines that don't name a specific company
        generic_patterns = [
            r"^Indian\s+(startups?|tech\s+startups?)",
            r"^India'?s\s+startups?",
            r"^Indian\s+AI",
            r"^AI\s+Becomes",
            r"^How\s+to",
            r"^Why\s+",
            r"^What\s+",
            r"^The\s+state\s+of",
            r"^Funding\s+",
            r"^Startup\s+funding",
            r"^A\s+\d+[-–]\s*minute\s+pitch",
            r"^\d+\s+minutes?",
            r"^Minutes?\s+with",
            r"^Meet\s+the",
            r"^Inside\s+the",
            r"^Inside\s+",
            r"^Exclusive:\s+",
            r"^Breaking:\s+",
            r"^Report:\s+",
        ]
        for pat in generic_patterns:
            if re.search(pat, title, re.IGNORECASE):
                return ""

        # Pattern: "Company Name raises/bags/secures/closes/gets/nets/lands/snags/picks up $X..."
        m = re.search(
            r"^([A-Z][a-zA-Z0-9\s\.\-&]+?)\s+(?:raises|bags|secures|closes|gets|nets|lands|snags|picks\s+up|wins)\s+",
            title,
            re.IGNORECASE,
        )
        if m:
            name = m.group(1).strip()
            # Strip common prefixes
            name = re.sub(r"^(?:Energy\s+Storage\s+Startup|Indian\s+American\s+startup|Bengaluru\s+food\s+delivery\s+startup|New\s+Delhi-based\s+consumer\s+appliance\s+startup|AI\s+music\s+startup|Nutrition\s+startup|Deeptech\s+startup|Fintech\s+startup|Healthtech\s+startup|Edtech\s+startup|Cleantech\s+startup|Agritech\s+startup|Logistics\s+startup|E-commerce\s+startup|SaaS\s+startup|AI\s+startup|ML\s+startup|Tech\s+startup|Food\s+tech\s+startup|EV\s+startup|Space\s+tech\s+startup|Cyber\s+security\s+startup|HR\s+tech\s+startup|Prop\s+tech\s+startup|Gaming\s+startup)\s+", "", name, flags=re.IGNORECASE)
            name = re.sub(r"[\:\-\s]+$", "", name)
            # Reject if name contains junk words
            if any(w in name for w in ["minute", "pitch", "minutes", "Meet", "Inside", "Exclusive", "Breaking", "Report"]):
                return ""
            if 2 <= len(name) <= 40:
                return name

        # Pattern: "Company Name in Series/Seed round..."
        m = re.search(
            r"^([A-Z][a-zA-Z0-9\s\.\-&]+?)\s+in\s+(?:Series|Seed|Pre-Series)\s+",
            title,
            re.IGNORECASE,
        )
        if m:
            name = m.group(1).strip()
            name = re.sub(r"^(?:Energy\s+Storage\s+Startup|Indian\s+American\s+startup|Bengaluru\s+food\s+delivery\s+startup|New\s+Delhi-based\s+consumer\s+appliance\s+startup|AI\s+music\s+startup|Nutrition\s+startup|Deeptech\s+startup|Fintech\s+startup|Healthtech\s+startup|Edtech\s+startup|Cleantech\s+startup|Agritech\s+startup|Logistics\s+startup|E-commerce\s+startup|SaaS\s+startup|AI\s+startup|ML\s+startup|Tech\s+startup|Food\s+tech\s+startup|EV\s+startup|Space\s+tech\s+startup|Cyber\s+security\s+startup|HR\s+tech\s+startup|Prop\s+tech\s+startup|Gaming\s+startup)\s+", "", name, flags=re.IGNORECASE)
            name = re.sub(r"[\:\-\s]+$", "", name)
            if any(w in name for w in ["minute", "pitch", "minutes", "Meet", "Inside", "Exclusive", "Breaking", "Report"]):
                return ""
            if 2 <= len(name) <= 40:
                return name

        # Pattern: "Company Name - something" (e.g., "Raghu Vamsi Aerospace bags $40M...")
        # Look for capitalized words before a funding verb
        m = re.search(
            r"^((?:[A-Z][a-zA-Z0-9\.\-&]+\s*)+?)\s+(?:raises|bags|secures|closes|gets|nets|lands|snags)",
            title,
            re.IGNORECASE,
        )
        if m:
            name = m.group(1).strip()
            name = re.sub(r"^(?:Energy\s+Storage\s+Startup|Indian\s+American\s+startup|Bengaluru\s+food\s+delivery\s+startup|New\s+Delhi-based\s+consumer\s+appliance\s+startup|AI\s+music\s+startup|Nutrition\s+startup|Deeptech\s+startup|Fintech\s+startup|Healthtech\s+startup|Edtech\s+startup|Cleantech\s+startup|Agritech\s+startup|Logistics\s+startup|E-commerce\s+startup|SaaS\s+startup|AI\s+startup|ML\s+startup|Tech\s+startup|Food\s+tech\s+startup|EV\s+startup|Space\s+tech\s+startup|Cyber\s+security\s+startup|HR\s+tech\s+startup|Prop\s+tech\s+startup|Gaming\s+startup)\s+", "", name, flags=re.IGNORECASE)
            name = re.sub(r"[\:\-\s]+$", "", name)
            if any(w in name for w in ["minute", "pitch", "minutes", "Meet", "Inside", "Exclusive", "Breaking", "Report"]):
                return ""
            if 2 <= len(name) <= 40:
                return name

        return ""

    def _extract_amount_from_text(self, text: str) -> str:
        """Extract funding amount from text."""
        _AMOUNT_RE = re.compile(
            r"(?:USD\s*)?\$[\d,\.]+\s*(?:million|billion|M|B|mn|bn)?|"
            r"₹[\d,\.]+\s*(?:crore|lakh|Cr|cr)?|"
            r"[\d,\.]+\s*(?:million|billion|crore|lakh)\s*(?:USD|INR|rupees)?",
            re.IGNORECASE,
        )
        m = _AMOUNT_RE.search(text)
        return m.group(0).strip() if m else "Undisclosed"

    def _extract_stage_from_text(self, text: str) -> str:
        """Extract funding stage from text."""
        _STAGE_RE = re.compile(
            r"\b(Pre-Seed|Seed|Pre-Series [A-D]|Series [A-E]\+?|Series [A-E]|"
            r"Growth|Unicorn|IPO|Bridge|Angel)\b",
            re.IGNORECASE,
        )
        m = _STAGE_RE.search(text)
        return m.group(0).title() if m else "Undisclosed"

    def _parse_article_for_companies(self, text: str) -> List[str]:
        """
        Parse article text and extract company names near funding mentions.
        Only looks at the first 1000 characters of the article to avoid
        extracting names from later paragraphs.
        Returns unique company names.
        """
        companies = []
        # Only look at the beginning of the article
        text = text[:1500]
        _FUNDING_RE = re.compile(
            r"\b(?:raised?|bags?|secures?|closes?|gets?|nets?|lands?|snags?|picks?\s+up|wins?)\s+"
            r"(?:\$[\d,\.]+\s*(?:million|billion|M|B|mn|bn)?|"
            r"₹[\d,\.]+\s*(?:crore|lakh|Cr|cr)?|"
            r"[\d,\.]+\s*(?:million|billion|crore|lakh)\s*(?:USD|INR|rupees)?)",
            re.IGNORECASE,
        )
        _STAGE_RE = re.compile(
            r"\b(Pre-Seed|Seed|Pre-Series [A-D]|Series [A-E]\+?|Series [A-E]|"
            r"Growth|Unicorn|IPO|Bridge|Angel)\b",
            re.IGNORECASE,
        )
        _COMPANY_RE = re.compile(
            r"\b([A-Z][a-zA-Z0-9\.\-&]{2,30}(?:\s+[A-Z][a-zA-Z0-9\.\-&]{2,30}){0,3})\b"
        )
        _BAD_WORDS = {
            "Indian", "India", "Startup", "Startups", "Bengaluru", "Mumbai", "Delhi",
            "Raised", "Secures", "Closes", "Bags", "Gets", "Nets", "Lands", "Snags",
            "The", "This", "That", "These", "Those", "Year", "Month", "Week", "Report",
            "According", "Based", "Founded", "Company", "Companies", "Funding", "Investment",
            "Investor", "Investors", "Venture", "Capital", "Round", "Rounds", "Series",
            "Seed", "Pre", "Post", "Early", "Late", "Growth", "Unicorn", "Soonicorn",
            "Startups", "Entrepreneur", "Entrepreneurs", "Founder", "Founders", "CEO",
            "CTO", "CFO", "COO", "Director", "Managing", "Partner", "General",
            "News", "Latest", "More", "Fewer", "After", "With", "Eye", "Becomes",
            "First", "New", "Tech", "AI", "ML", "Data", "Digital", "Health", "Fintech",
            "Edtech", "Deeptech", "SaaS", "B2B", "B2C", "C2C", "API", "Sdk",
            "Ncr", "Gurgaon", "Gurugram", "Noida", "Hyderabad", "Pune", "Chennai",
            "Kolkata", "Ahmedabad", "Jaipur", "Surat", "Lucknow", "Kanpur", "Indore",
            "Bhopal", "Patna", "Ranchi", "Bhubaneswar", "Coimbatore", "Mysore",
            "Vijayawada", "Visakhapatnam", "Kochi", "Trivandrum", "Chandigarh",
            "Mohali", "Zirakpur", "Faridabad", "Ghaziabad", "Navi", "Thane", "Kalyan",
            "Dombivli", "Vasai", "Virar", "Panvel", "Karjat", "Kharghar", "Vashi",
            "Nerul", "Belapur", "Airoli", "Rabale", "Turbhe", "Juinagar", "Seawoods",
            "Darave", "Koparkhairane", "Ghansoli", "Dighe", "Juhu", "Bandra", "Andheri",
            "Jogeshwari", "Goregaon", "Malad", "Kandivali", "Borivali", "Dahisar",
            "Mira", "Bhayandar", "Naigaon", "Palghar", "Tulsi", "Kashimira", "Oshiwara",
            "Elon", "Musk", "Reid", "Hoffman", "Mark", "Pincus", "Ritankar", "Das",
            "Marina", "Temkin", "David", "Paul", "Morris", "Bloomberg", "PDT", "July",
            "Other", "Titan", "Insight", "Partners", "Image", "Credits", "IPO",
        }

        # Find sentences with funding mentions
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for sentence in sentences[:5]:  # Only look at first 5 sentences
            if not _FUNDING_RE.search(sentence) and not _STAGE_RE.search(sentence):
                continue
            # Extract potential company names (2-4 capitalized words)
            candidates = _COMPANY_RE.findall(sentence)
            for candidate in candidates:
                candidate = candidate.strip()
                if len(candidate) < 2 or len(candidate) > 40:
                    continue
                words = candidate.split()
                if any(w in _BAD_WORDS for w in words):
                    continue
                if any(w.lower() in {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "from", "up", "down", "out", "so", "no", "yes", "it", "its", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would", "shall", "should", "may", "might", "must", "can", "could", "i", "you", "he", "she", "we", "they", "me", "him", "her", "us", "them"} for w in words):
                    continue
                if candidate.lower() not in {c.lower() for c in companies}:
                    companies.append(candidate)

        return companies

    def scrape_techcrunch_rss(self) -> List[Dict[str, Any]]:
        """Scrape TechCrunch RSS for India startup funding."""
        results = []
        try:
            url = "https://techcrunch.com/feed/"
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "xml")
                items = soup.find_all("item")
                for item in items:
                    title = item.find("title")
                    link = item.find("link")
                    if not title or not link:
                        continue
                    title_text = title.text.strip()
                    if not any(kw in title_text.lower() for kw in ["fund", "raise", "invest", "series", "seed", "crore", "million"]):
                        continue
                    company = self._extract_company_from_news_title(title_text)
                    if not company:
                        continue
                    results.append({
                        "title": title_text,
                        "url": link.text.strip(),
                        "snippet": title_text,
                        "source": "TechCrunch RSS",
                        "date": "",
                        "company_name": company,
                        "funding_amount": self._extract_amount_from_text(title_text),
                        "funding_stage": self._extract_stage_from_text(title_text),
                    })
                logger.info(f"TechCrunch RSS returned {len(results)} articles")
        except Exception as e:
            logger.debug(f"TechCrunch RSS failed: {e}")
        return results

    def scrape_startup_india(self) -> List[Dict[str, Any]]:
        """Scrape Startup India API."""
        results = []
        try:
            resp = self.session.get("https://www.startupindia.gov.in/api/v1/startups/search?limit=50", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("data", [])[:20]:
                    name = item.get("name", "")
                    if name:
                        results.append({
                            "company_name": name,
                            "website": item.get("website", ""),
                            "industry": item.get("sector", "Technology"),
                            "city": item.get("city", "India"),
                            "state": "",
                            "funding_stage": "Funded",
                            "funding_amount": "Undisclosed",
                            "latest_funding_date": "2025-01-01",
                            "investors": "Startup India",
                            "company_description": item.get("description", ""),
                            "source": "Startup India API",
                            "source_url": "https://www.startupindia.gov.in",
                        })
                logger.info(f"Startup India API returned {len(results)} entries")
        except Exception as e:
            logger.debug(f"Startup India API failed: {e}")
        return results

    @staticmethod
    def _parse_funding_page(text: str, url: str, source: str) -> List[Dict[str, Any]]:
        """Parse funding mentions from page text using strict regex."""
        import datetime

        results = []
        _AMOUNT_RE = re.compile(
            r"(?:USD\s*)?\$[\d,\.]+\s*(?:million|billion|M|B|mn|bn)?|"
            r"₹[\d,\.]+\s*(?:crore|lakh|Cr|cr)?|"
            r"[\d,\.]+\s*(?:million|billion|crore|lakh)\s*(?:USD|INR|rupees)?",
            re.IGNORECASE,
        )
        _STAGE_RE = re.compile(
            r"\b(Pre-Seed|Seed|Pre-Series [A-D]|Series [A-E]\+?|Series [A-E]|"
            r"Growth|Unicorn|IPO|Bridge|Angel)\b",
            re.IGNORECASE,
        )

        # Split by date patterns to get individual article snippets
        chunks = re.split(r"(?=\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})", text, flags=re.IGNORECASE)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk or len(chunk) < 20:
                continue
            if not any(kw in chunk.lower() for kw in ["fund", "raise", "invest", "series", "seed", "crore", "million", "billion"]):
                continue

            amount_m = _AMOUNT_RE.search(chunk)
            stage_m = _STAGE_RE.search(chunk)

            # Remove date prefix and trailing "Remove" buttons
            clean_chunk = re.sub(r"^\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\s+", "", chunk, flags=re.IGNORECASE)
            clean_chunk = re.sub(r"\s*Remove\s*$", "", clean_chunk).strip()
            clean_chunk = re.sub(r"\s+", " ", clean_chunk)

            # Try to extract company name using strict patterns
            company_name = ""
            m = re.search(
                r"([A-Z][a-zA-Z0-9\s\.\-&]+?)\s+(?:raises?|bags?|secures?|closes?|gets?|nets?|lands?|snags?)\s+",
                clean_chunk,
                re.IGNORECASE,
            )
            if m:
                company_name = m.group(1).strip()
                company_name = re.sub(r"[\:\-\s]+$", "", company_name)

            if not company_name:
                m = re.search(
                    r"([A-Z][a-zA-Z0-9\s\.\-&]+?)\s+in\s+(?:Series|Seed|Pre-Series)\s+",
                    clean_chunk,
                    re.IGNORECASE,
                )
                if m:
                    company_name = m.group(1).strip()
                    company_name = re.sub(r"[\:\-\s]+$", "", company_name)

            if not company_name or len(company_name) < 3 or len(company_name) > 40:
                continue

            # Validate company name
            bad_words = {"Indian", "India", "Startup", "Startups", "Bengaluru", "Mumbai", "Delhi",
                         "The", "This", "That", "These", "Those", "Year", "Month", "Week", "Report",
                         "According", "Based", "Funding", "Investment", "Investor", "Venture",
                         "Capital", "Round", "Series", "Seed", "Pre", "Post", "Early", "Late",
                         "News", "Latest", "More", "Fewer", "After", "With", "Eye", "Becomes",
                         "First", "New", "Tech", "AI", "ML", "Data", "Digital", "Health", "Remove"}
            words = company_name.split()
            if any(w in bad_words for w in words):
                continue
            if any(w.lower() in {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "from"} for w in words):
                continue

            results.append({
                "company_name": company_name,
                "website": f"https://{company_name.lower().replace(' ', '')}.com",
                "industry": "Technology",
                "city": "India",
                "state": "",
                "funding_stage": stage_m.group(0).title() if stage_m else "Undisclosed",
                "funding_amount": amount_m.group(0).strip() if amount_m else "Undisclosed",
                "latest_funding_date": datetime.date.today().isoformat(),
                "investors": "See article",
                "company_description": clean_chunk[:220],
                "source": source,
                "source_url": url,
            })
        return results

    def linkedin_search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Search LinkedIn profiles using DuckDuckGo.
        Returns list of {name, url, headline, company, location}.
        """
        try:
            from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(f"site:linkedin.com/in {query}", max_results=max_results):
                    url = r.get("href", "")
                    if "linkedin.com/in/" not in url:
                        continue
                    results.append({
                        "name": r.get("title", "").split(" - ")[0].strip(),
                        "url": url,
                        "headline": r.get("body", ""),
                        "company": "",
                        "location": "",
                        "source": "LinkedIn (DDG)",
                    })
            logger.info(f"LinkedIn DDG search returned {len(results)} profiles for: {query[:50]}")
            return results
        except Exception as e:
            logger.debug(f"LinkedIn DDG search failed: {e}")
            return []

    def google_search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Google search using Bing HTML scraping + DDG fallback.
        Returns list of {title, url, snippet, source}.
        """
        results = self._bing_search(query, max_results)
        if len(results) >= max_results:
            return results[:max_results]

        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    if len(results) >= max_results:
                        break
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                        "source": "Google (DDG)",
                    })
            if results:
                logger.info(f"Search returned {len(results)} results for: {query[:50]}")
        except Exception as e:
            logger.debug(f"DDG search failed: {e}")
        return results[:max_results]

    def _bing_search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Direct Bing HTML scraping for search results.
        """
        try:
            results = []
            for offset in [0, 11, 21]:
                if len(results) >= max_results:
                    break
                url = f"https://www.bing.com/search?q={requests.utils.quote(query)}&first={offset + 1}&FORM=PERE"
                resp = self.session.get(url, timeout=10)
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "lxml")
                for li in soup.select("li.b_algo")[:max_results - len(results)]:
                    title_el = li.find("h2")
                    title = title_el.get_text(strip=True) if title_el else ""
                    link_el = title_el.find("a", href=True) if title_el else None
                    url = link_el["href"] if link_el else ""
                    snippet_el = li.find("p")
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                    if title and url and url.startswith("http"):
                        results.append({
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "Bing",
                        })
            if results:
                logger.info(f"Bing returned {len(results)} results for: {query[:50]}")
            return results
        except Exception as e:
            logger.debug(f"Bing search failed: {e}")
            return []

    def news_search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        News search using Bing News HTML scraping + DDG fallback.
        Returns list of {title, url, snippet, source, date}.
        """
        results = self._bing_news_search(query, max_results)
        if len(results) >= max_results:
            return results[:max_results]

        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                for r in ddgs.news(query, max_results=max_results):
                    if len(results) >= max_results:
                        break
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("body", ""),
                        "source": r.get("source", "News"),
                        "date": r.get("date", ""),
                    })
            if results:
                logger.info(f"News search returned {len(results)} results for: {query[:50]}")
        except Exception as e:
            logger.debug(f"DDG news search failed: {e}")
        return results[:max_results]

    def _bing_news_search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Direct Bing News HTML scraping.
        """
        try:
            results = []
            url = f"https://www.bing.com/news/search?q={requests.utils.quote(query)}&qft=sortbydate%3d%221%22"
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                for card in soup.select(".news-card, .newsitem, .b_algo")[:max_results]:
                    title_el = card.find("a")
                    title = title_el.get_text(strip=True) if title_el else ""
                    url = title_el["href"] if title_el and title_el.get("href") else ""
                    snippet_el = card.find("p") or card.find(".snippet")
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                    date_el = card.find(".date") or card.find("[data-*='date']")
                    date = date_el.get_text(strip=True) if date_el else ""
                    if title and url and url.startswith("http"):
                        results.append({
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "Bing News",
                            "date": date,
                        })
            if results:
                logger.info(f"Bing News returned {len(results)} results for: {query[:50]}")
            return results
        except Exception as e:
            logger.debug(f"Bing News failed: {e}")
            return []
