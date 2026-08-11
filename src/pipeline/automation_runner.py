"""
Multi-source Apify-powered Startup Intelligence Pipeline.

Stages:
  1. Validate config (Apify token, Gemini key, paths)
  2. Multi-source startup discovery (Apify-powered)
  3. Filter for early-stage (founding year, employee count, stage)
  4. Gemini AI processing (normalize, deduplicate, verify, summarize, score)
  5. Website crawling for contacts (async batch via Apify)
  6. Export to Excel (append-only, 6 sheets)
"""
import sys
import logging
import logging.handlers
import datetime
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.config          import (
    GEMINI_API_KEY, GEMINI_MODEL, EXCEL_OUTPUT_PATH, LOG_FILE_PATH,
    DB_PATH, PIPELINE_STARTUP_LIMIT, validate_config,
)
from src.scrapers.startup_discovery   import IndianStartupDiscoverer
from src.scrapers.leadership_hiring   import LeadershipHiringScraper
from src.ai.gemini_processor          import GeminiAIProcessor
from src.exporters.excel_exporter     import Exporter

Path(LOG_FILE_PATH).parent.mkdir(parents=True, exist_ok=True)
file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE_PATH, mode="a", encoding="utf-8", maxBytes=10*1024*1024, backupCount=5
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        file_handler,
    ],
)
logger = logging.getLogger("PipelineRunner")


class IndianStartupIntelligenceWorkflow:
    """Orchestrates the multi-source Apify funding & founder intelligence pipeline."""

    def __init__(self):
        issues = validate_config()
        for issue in issues:
            logger.warning(f"Config issue: {issue}")

        # Core Components
        self.gemini = GeminiAIProcessor(api_key=GEMINI_API_KEY, model_name=GEMINI_MODEL)
        
        self.discoverer = IndianStartupDiscoverer(db_path=DB_PATH)
        self.discoverer.set_gemini(self.gemini)
        
        self.contact_scraper = LeadershipHiringScraper()
        self.contact_scraper.set_gemini(self.gemini)

        Path(EXCEL_OUTPUT_PATH).parent.mkdir(parents=True, exist_ok=True)
        self.exporter = Exporter(excel_path=EXCEL_OUTPUT_PATH)

        self.last_summary: dict = {}
        logger.info("IndianStartupIntelligenceWorkflow initialised (Apify-powered).")

    def run_daily_workflow(self, limit: int = None) -> dict:
        limit = limit or PIPELINE_STARTUP_LIMIT
        start_ts = datetime.datetime.now()
        errors = 0

        logger.info("=" * 70)
        logger.info(f"PIPELINE START  -  {start_ts.strftime('%Y-%m-%d %H:%M:%S IST')}")
        logger.info("=" * 70)

        # Stage 2 & 3: Discovery and initial filtering are handled internally by discoverer
        logger.info("STAGE 2 & 3 - Discovering & filtering multi-source Indian startups...")
        raw_startups = self.discoverer.discover_recent_startups(limit=limit)
        
        if not raw_startups:
            logger.warning("No new startups found.")
            summary = self._build_summary(start_ts, 0, 0, 0, EXCEL_OUTPUT_PATH)
            return summary

        # Stage 4: AI Processing (Normalize, deduplicate, verify, summarize, score)
        logger.info("STAGE 4 - Gemini AI Processing (clean, dedup, score, summarize)...")
        cleaned_startups = self.gemini.process_and_clean_startups(raw_startups)

        # Limit to configured PIPELINE_STARTUP_LIMIT
        cleaned_startups = cleaned_startups[:limit]
        logger.info(f"  -> Processing {len(cleaned_startups)} highly ranked startups for contacts")

        # Stage 5: Website Crawling
        logger.info("STAGE 5 - Apify Website Crawling for leadership and contacts...")
        enrichments = {}
        contacts_found = 0
        founders_found = 0

        # We can batch process the contact scraping
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_startup = {
                executor.submit(self.contact_scraper.scrape_for_startup, company): company
                for company in cleaned_startups
            }
            
            for future in concurrent.futures.as_completed(future_to_startup):
                company = future_to_startup[future]
                c_name = company.get("company_name", "")
                try:
                    results = future.result()
                    
                    # Generate personalized outreach email if we found contacts
                    if results["contacts"]:
                        contact = results["contacts"][0]
                        outreach = self.gemini.generate_personalized_outreach(
                            company_name=c_name,
                            contact_name=contact.get("name", "Founding Team"),
                            role=contact.get("role", "Founder/Executive"),
                            funding_stage=company.get("funding_stage", "recently funded"),
                            open_jobs=[],
                            ai_summary=company.get("ai_summary", "")
                        )
                        results["outreach"] = outreach
                    
                    enrichments[c_name] = results
                    founders_found += len(results.get("leadership", {}).get("founders", []))
                    contacts_found += len(results.get("contacts", []))
                    
                except Exception as e:
                    logger.warning(f"Contact discovery failed for {c_name}: {e}")
                    errors += 1
                    enrichments[c_name] = {}

        # Stage 6: Excel Export
        logger.info("STAGE 6 - Exporting to 6-sheet Excel workbook (append-only)...")
        self.exporter.export(startups_data=cleaned_startups, enrichments=enrichments)

        summary = self._build_summary(
            start_ts,
            len(cleaned_startups),
            founders_found,
            contacts_found,
            EXCEL_OUTPUT_PATH,
        )
        self.last_summary = summary

        logger.info("=" * 70)
        logger.info(f"PIPELINE COMPLETE - {summary}")
        logger.info("=" * 70)
        return summary

    @staticmethod
    def _build_summary(start_ts, startups, founders, contacts, excel_path) -> dict:
        duration = (datetime.datetime.now() - start_ts).total_seconds()
        return {
            "timestamp":          start_ts.isoformat(),
            "status":             "SUCCESS",
            "duration_seconds":   round(duration, 1),
            "startups_found":     startups,
            "founders_found":     founders,
            "contacts_found":     contacts,
            "excel_path":         excel_path
        }


if __name__ == "__main__":
    workflow = IndianStartupIntelligenceWorkflow()
    result   = workflow.run_daily_workflow()
    print("\nPipeline Summary:")
    for k, v in result.items():
        print(f"  {k}: {v}")
