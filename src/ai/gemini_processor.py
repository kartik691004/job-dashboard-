"""
Real Gemini AI integration using google-genai SDK (v2).

Responsibilities:
  1. extract_startup_from_text      - Parse article text -> structured startup JSON
  2. extract_leadership_from_text   - Parse team page -> leadership & contacts JSON
  3. normalize_company_name         - Clean up company names
  4. verify_website_domain          - Check if domain matches company
  5. deduplicate_startups           - Fuzzy deduplication
  6. process_and_clean_startups     - Deduplicate, score & summarise startup list
  7. generate_company_summary       - Generate 2-sentence description
  8. calculate_growth_outreach_score - AI enhanced scoring
  9. generate_personalized_outreach - Draft personalised 3-touch email sequence
"""
import re
import json
import time
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("GeminiProcessor")


class GeminiAIProcessor:
    """
    Wrapper around google-genai SDK (v2).
    All methods handle rate limiting, retries, and JSON parse errors gracefully.
    """

    def __init__(self, api_key: str = "", model_name: str = "gemini-2.5-flash"):
        self.api_key    = api_key
        self.model_name = model_name
        self.client     = None
        self._rpm_log: List[float] = []

        if api_key and api_key != "YOUR_GEMINI_API_KEY_HERE":
            self._init_client()
        else:
            logger.warning("Gemini API key not set - AI features will be skipped.")

    _MODEL_FALLBACKS = (
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    )

    def _init_client(self):
        try:
            from google import genai
            from google.genai import types

            self.client = genai.Client(api_key=self.api_key)
            self._types = types
            self.model_name = self._resolve_model()
            logger.info(f"Gemini model '{self.model_name}' initialised successfully.")
        except Exception as e:
            logger.error(f"Gemini init failed: {e}")
            self.client = None
            self._types = None

    def _resolve_model(self) -> str:
        candidates = []
        for name in (self.model_name, *self._MODEL_FALLBACKS):
            if name and name not in candidates:
                candidates.append(name)

        for model_name in candidates:
            try:
                self.client.models.get(model=model_name)
                return model_name
            except Exception:
                continue
        logger.error("Gemini init failed - no supported model available.")
        return ""

    def _get_generate_config(self) -> Optional[Any]:
        if not self._types:
            return None
        return self._types.GenerateContentConfig(
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=2048,
            safety_settings=[
                self._types.SafetySetting(
                    category=self._types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                    threshold=self._types.HarmBlockThreshold.BLOCK_NONE,
                ),
                self._types.SafetySetting(
                    category=self._types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    threshold=self._types.HarmBlockThreshold.BLOCK_NONE,
                ),
                self._types.SafetySetting(
                    category=self._types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                    threshold=self._types.HarmBlockThreshold.BLOCK_NONE,
                ),
                self._types.SafetySetting(
                    category=self._types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    threshold=self._types.HarmBlockThreshold.BLOCK_NONE,
                ),
            ],
            response_mime_type="application/json"
        )

    def _call(self, prompt: str, retries: int = 2) -> str:
        if not self.client or not self.model_name:
            return ""
        config = self._get_generate_config()

        now = time.time()
        self._rpm_log = [t for t in self._rpm_log if now - t < 60]
        if len(self._rpm_log) >= 14:
            sleep_for = 62 - (now - self._rpm_log[0])
            logger.info(f"Approaching Gemini RPM limit - sleeping {sleep_for:.1f}s")
            time.sleep(max(sleep_for, 0))
        self._rpm_log.append(time.time())

        for attempt in range(retries):
            try:
                resp = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                )
                return resp.text or ""
            except Exception as e:
                msg = str(e)
                if "429" in msg or "quota" in msg.lower():
                    wait = 60 + attempt * 30
                    logger.warning(f"Gemini quota hit - sleeping {wait}s")
                    time.sleep(wait)
                else:
                    logger.warning(f"Gemini call error [{attempt+1}/{retries}]: {e}")
                    if attempt < retries - 1:
                        time.sleep(5)
        return ""

    @staticmethod
    def _parse_json(text: str) -> Any:
        try:
            # First try direct parsing (API might return clean JSON)
            return json.loads(text)
        except json.JSONDecodeError:
            pass
            
        try:
            # Fallback to finding JSON block
            text = text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        except json.JSONDecodeError as e:
            logger.debug(f"JSON Parse error: {e}")
            return None

    # ── Text Extraction ──────────────────────────────────────────────────────

    def extract_startup_from_text(self, text: str, source_url: str = "") -> List[Dict]:
        """Extract structured startup info from news text."""
        if not self.client:
            return []
        prompt = f"""
        Extract information about recently funded Indian startups from the text below.
        Return a JSON array of objects. Each object MUST have exactly these keys:
        - "company_name"
        - "website"
        - "industry"
        - "city"
        - "state"
        - "funding_stage"
        - "funding_amount"
        - "investors"
        - "company_description"
        
        Use "Undisclosed" if unknown. Return [] if no startups found.
        Text: {text[:3500]}
        """
        raw = self._call(prompt)
        parsed = self._parse_json(raw)
        if not isinstance(parsed, list):
            parsed = []
            
        for r in parsed:
            r["source"] = source_url
            r["ai_sourced"] = True
            r["latest_funding_date"] = __import__("datetime").date.today().isoformat()
            r["founding_year"] = ""
            r["employee_count"] = ""
            
        return parsed

    def extract_leadership_from_text(self, company: str, text: str) -> Dict[str, Any]:
        """Parse website text to extract founders, execs, and contacts."""
        if not self.client:
            return {}
        prompt = f"""
        Extract leadership and key contacts for the startup '{company}' from the text below.
        Return a JSON object with this exact structure:
        {{
            "founders": ["Name 1", "Name 2"],
            "ceo": "Name",
            "cto": "Name",
            "people": [
                {{
                    "name": "Name",
                    "role": "Role/Title",
                    "email": "Email if mentioned, else empty",
                    "linkedin_url": "URL if mentioned, else empty"
                }}
            ]
        }}
        Return empty arrays/strings if not found. Only include founders/executives/HR.
        Text: {text[:8000]}
        """
        raw = self._call(prompt)
        parsed = self._parse_json(raw)
        if isinstance(parsed, dict):
            return parsed
        return {}

    # ── Core Enhancements ────────────────────────────────────────────────────

    def normalize_company_name(self, name: str) -> str:
        """Clean up company name suffixes (e.g., 'Technologies Pvt Ltd' -> '')."""
        name = name.strip()
        # Basic fast regex cleanup first
        name = re.sub(r'(?i)\s+(private|pvt|ltd|limited|inc|corp|corporation|llc|co)\.?$', '', name).strip()

        # Fast-path: skip the Gemini call when the name is already clean & properly cased.
        # This avoids burning quota on simple names (Gemini free tier is heavily rate-limited).
        if (
            len(name) > 3
            and not re.search(r'\s(technologies|solutions|services|systems|software)\b', name, re.IGNORECASE)
            and name == name.title()
        ):
            return name

        # If Gemini is available, use it for smarter normalization of weird casings
        if self.client and len(name) > 3:
            prompt = f"""
            Normalize the following Indian startup company name. 
            Remove legal entities (Pvt Ltd, Technologies, Solutions), fix weird casing, 
            and return ONLY the clean brand name as a JSON string.
            Example: "RazorPay Software Pvt. Ltd." -> {{"name": "Razorpay"}}
            Input: "{name}"
            """
            raw = self._call(prompt)
            parsed = self._parse_json(raw)
            if isinstance(parsed, dict) and "name" in parsed:
                return parsed["name"].strip()
                
        return name.title() if name.islower() or name.isupper() else name

    def verify_website_domain(self, company: str, url: str) -> bool:
        """AI check if URL plausibly matches the company name."""
        if not self.client or not url:
            return True # Assume True if we can't check
            
        prompt = f"""
        Does the website URL '{url}' plausibly belong to the Indian startup '{company}'?
        Consider acronyms, common brand URL formats, and generic news sites (news sites = false).
        Return JSON: {{"matches": true/false}}
        """
        raw = self._call(prompt)
        parsed = self._parse_json(raw)
        if isinstance(parsed, dict):
            return parsed.get("matches", True)
        return True

    def deduplicate_startups(self, startups: List[Dict]) -> List[Dict]:
        """Fuzzy AI deduplication of a batch of startups."""
        if not self.client or len(startups) < 2:
            return startups
            
        # Give Gemini a simplified list
        simple_list = [{"id": i, "name": s.get("company_name", ""), "website": s.get("website", "")} 
                       for i, s in enumerate(startups)]
                       
        prompt = f"""
        Identify duplicates in this list of startup companies. Consider slight name variations 
        and domain matches.
        Return a JSON array of arrays, where each inner array contains the IDs of startups that are the same entity.
        Example: [[0, 2], [1, 3, 4], [5]]
        Input: {json.dumps(simple_list)}
        """
        raw = self._call(prompt)
        parsed = self._parse_json(raw)
        
        if not isinstance(parsed, list):
            return startups
            
        # Keep the first ID from each duplicate group
        keep_ids = set()
        for group in parsed:
            if isinstance(group, list) and len(group) > 0:
                keep_ids.add(group[0])
                
        deduped = [s for i, s in enumerate(startups) if i in keep_ids or not any(i in g for g in parsed if isinstance(g, list))]
        logger.info(f"AI Deduplication: reduced {len(startups)} to {len(deduped)}")
        return deduped

    def generate_company_summary(self, startup: Dict) -> str:
        """Generate a clean 2-sentence company description."""
        name = startup.get("company_name", "")
        desc = startup.get("company_description", "")
        ind = startup.get("industry", "")
        
        # Fast-path: reuse a strong existing description instead of burning a Gemini call.
        # Free-tier Gemini is heavily rate-limited, so use it only when we lack context.
        if desc and len(desc) > 60:
            return re.sub(r"\s{2,}", " ", desc.strip())[:300]
        
        if not self.client:
            return desc[:200]
            
        prompt = f"""
        Write a concise, professional 1-2 sentence description for the Indian startup '{name}'.
        Industry: {ind}
        Raw context: {desc[:1000]}
        
        Return JSON: {{"summary": "..."}}
        Focus on what problem they solve and what their product is. 
        """
        raw = self._call(prompt)
        parsed = self._parse_json(raw)
        if isinstance(parsed, dict) and "summary" in parsed:
            return parsed["summary"]
            
        return desc[:200]

    def calculate_growth_outreach_score(self, startup: Dict) -> int:
        """Calculate AI-enhanced outreach score (0-100)."""
        score = 0
        stage = (startup.get("funding_stage") or "").lower()
        amount_str = (startup.get("funding_amount") or "").lower()
        year = startup.get("founding_year", "")
        emp = startup.get("employee_count", "")
        
        # Base scoring
        # Early stage = better for this specific use case (Pre-Seed, Seed, Angel)
        if "pre-seed" in stage or "seed" in stage or "angel" in stage:
            score += 40
        elif "series a" in stage:
            score += 30
        elif "bootstrap" in stage:
            score += 25
        elif "undisclosed" in stage:
            score += 15
        
        # Year scoring (newer is better for outreach)
        if year.isdigit():
            y = int(year)
            if y >= 2023: score += 20
            elif y >= 2021: score += 15
            elif y >= 2019: score += 5
            
        # Amount scoring (some funding is good, too much = probably well established)
        if "$" in amount_str or "rs" in amount_str or "cr" in amount_str:
            if "million" in amount_str or "m" in amount_str:
                score += 15
            else:
                score += 10
                
        # Employees (smaller is better for founder-led outreach)
        if emp:
            if "1-10" in emp or "11-50" in emp:
                score += 20
            elif "51-200" in emp:
                score += 10

        # Hiring-frequency signal: frequent job postings across job platforms
        # (Instahyre, Cutshort, Internshala, Unstop, Indeed, LinkedIn Jobs)
        # indicate an active post-funding hiring spree -> higher priority.
        posting_count = startup.get("_job_posting_count") or startup.get("_linkedin_posting_count") or 0
        if posting_count:
            if posting_count >= 5:
                score += 25
            elif posting_count >= 3:
                score += 15
            else:
                score += 8
            # Smaller gap between postings = hiring more urgently
            gap = startup.get("_job_avg_gap_days") or startup.get("_linkedin_avg_gap_days") or 0
            if gap and gap <= 3:
                score += 5

        return min(score, 100)

    # ── High Level Orchestration ─────────────────────────────────────────────

    def process_and_clean_startups(self, raw_startups: List[Dict]) -> List[Dict]:
        """Normalises names, dedups, scores, and summarises a batch of startups."""
        logger.info(f"AI Processing {len(raw_startups)} raw startup records...")
        
        # 1. Normalize names (locally for speed, AI is too slow for 100s of names sequentially)
        for s in raw_startups:
            s["company_name"] = self.normalize_company_name(s.get("company_name", ""))
            
        # 2. Local dedup by normalized name
        unique_startups = {}
        for s in raw_startups:
            name = s["company_name"].lower()
            if name not in unique_startups:
                unique_startups[name] = s
        
        startups = list(unique_startups.values())
        
        # 3. AI Fuzzy Dedup (batch of max 20 for safety)
        if self.client and len(startups) <= 50:
             startups = self.deduplicate_startups(startups)
             
        # 4. Score and summarize
        for item in startups:
            score = self.calculate_growth_outreach_score(item)
            item["priority_score"] = score
            
            if score >= 60:
                item["outreach_priority"] = "Tier 1 - High"
            elif score >= 40:
                item["outreach_priority"] = "Tier 2 - Medium"
            else:
                item["outreach_priority"] = "Tier 3 - Low"
                
            item["ai_summary"] = self.generate_company_summary(item)
            item["deduplicated"] = True

        startups.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
        logger.info(f"AI Processing complete: {len(startups)} unique scored startups")
        return startups

    def generate_personalized_outreach(
        self,
        company_name: str,
        contact_name: str,
        role: str,
        funding_stage: str,
        open_jobs: List[Dict] = None,
        ai_summary: str = "",
    ) -> Dict[str, str]:
        """Draft a 3-touch sequence."""
        open_jobs = open_jobs or []
        first_name = contact_name.split()[0] if contact_name else "there"

        if self.client:
            prompt = f"""
            You are a business development professional reaching out to {company_name}, a {funding_stage} Indian startup.
            Contact: {contact_name} ({role})
            Company context: {ai_summary or f'{company_name} is a {funding_stage} startup.'}
            
            Write a 3-email outreach sequence. Return JSON:
            {{
              "email_subject": "...",
              "initial_pitch": "...(3-4 sentences, professional, personalised)",
              "day3_followup": "...(2-3 sentences, add value)",
              "day7_followup": "...(2 sentences, gentle CTA)"
            }}
            """
            raw = self._call(prompt)
            data = self._parse_json(raw)
            if isinstance(data, dict) and "initial_pitch" in data:
                return {
                    "company":       company_name,
                    "contact":       contact_name,
                    "role":          role,
                    "email_subject": data.get("email_subject", f"Opportunity for {company_name}"),
                    "initial_pitch": data.get("initial_pitch", ""),
                    "day3_followup": data.get("day3_followup", ""),
                    "day7_followup": data.get("day7_followup", ""),
                    "email_status":  "AI_DRAFT_READY",
                    "notes":         "AI-generated sequence",
                }

        # Fallback template
        return {
            "company":       company_name,
            "contact":       contact_name,
            "role":          role,
            "email_subject": f"Collaboration Opportunity - {company_name} ({funding_stage})",
            "initial_pitch": (
                f"Hi {first_name},\n\n"
                f"Congratulations on {company_name}'s exciting momentum at the {funding_stage} stage! "
                f"We work with high-growth Indian tech startups to accelerate business development and "
                f"talent acquisition. I'd love to explore how we can support {company_name}'s growth this quarter.\n\n"
                f"Would you be open to a 10-minute call early next week?\n\nBest,\nOutreach Team"
            ),
            "day3_followup": (
                f"Hi {first_name}, following up on my note from earlier. "
                f"Happy to share a quick case study on how we've helped similar startups. "
                f"Would Tuesday or Wednesday work for a brief call?"
            ),
            "day7_followup": (
                f"Hi {first_name}, last note from my side - if the timing isn't right, no worries at all. "
                f"Feel free to reach out whenever works best for {company_name}. Wishing you a great quarter!"
            ),
            "email_status": "TEMPLATE_DRAFT",
            "notes": "Template draft",
        }
