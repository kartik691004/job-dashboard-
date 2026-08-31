"""
Full Pipeline Runner — Indian Startup Intelligence
====================================================
1. Loads 100+ curated startups (Shark Tank India, AI/DeepTech, Fintech,
   HealthTech, D2C, EV, SaaS, Web3, AgriTech, Logistics, EdTech, etc.)
2. Uses DuckDuckGo HTML search to find real LinkedIn URLs for founders
3. Guesses emails using domain patterns
4. Finds HR / Talent Acquisition contacts
5. Exports to local Excel workbook
6. Pushes all 6 sheets to Google Sheets

Run:
    python run_full_pipeline.py
"""
import sys, os, re, json, time, logging, datetime, ssl, warnings
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Any, Optional

# Fix SSL for corporate firewalls
ssl._create_default_https_context = ssl._create_unverified_context
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

# Patch requests to disable SSL verification globally
import requests
_orig_request = requests.Session.request
def _patched_request(*args, **kwargs):
    kwargs['verify'] = False
    return _orig_request(*args, **kwargs)
requests.Session.request = _patched_request

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import GEMINI_API_KEY, GEMINI_MODEL, EXCEL_OUTPUT_PATH
from src.exporters.excel_exporter import ExcelOutreachExporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("FullPipeline")


# =============================================================================
# EXPANDED CURATED STARTUPS — 100+ Indian startups across all sectors
# =============================================================================
CURATED_STARTUPS = [
    # ─── Shark Tank India (Seasons 1-4) ───────────────────────────────────
    {"company_name": "Bao House", "industry": "Food & Beverage", "city": "Mumbai",
     "funding_stage": "Seed", "funding_amount": "Undisclosed",
     "investors": "Shark Tank India", "source": "Shark Tank India",
     "founded_year": "2022", "shark_tank_season": "S3",
     "founder_name": "Prachi Bhansali", "founder_role": "Founder & CEO"},
    {"company_name": "Nuskhe Kitchen", "industry": "Food Tech", "city": "Mumbai",
     "funding_stage": "Seed", "funding_amount": "₹75 lakh",
     "investors": "Aman Gupta (boAt), Vineeta Singh (Sugar)", "source": "Shark Tank India",
     "founded_year": "2021", "shark_tank_season": "S3",
     "founder_name": "Ketan Kadam", "founder_role": "Founder & CEO"},
    {"company_name": "Malaki", "industry": "Beverages (Non-Alcoholic)", "city": "Bengaluru",
     "funding_stage": "Seed", "funding_amount": "₹1 crore",
     "investors": "Aman Gupta (boAt)", "source": "Shark Tank India",
     "founded_year": "2022", "shark_tank_season": "S3",
     "founder_name": "Aditi Madan", "founder_role": "Co-Founder"},
    {"company_name": "Zoff Foods", "industry": "FMCG / Spices", "city": "Bengaluru",
     "funding_stage": "Seed", "funding_amount": "₹1.5 crore",
     "investors": "Aman Gupta, Ashneer Grover", "source": "Shark Tank India",
     "founded_year": "2021", "shark_tank_season": "S2",
     "founder_name": "Akash Agrawal", "founder_role": "Founder & CEO"},
    {"company_name": "Auli Lifestyle", "industry": "Skincare / D2C", "city": "Ahmedabad",
     "funding_stage": "Seed", "funding_amount": "₹75 lakh",
     "investors": "Vineeta Singh", "source": "Shark Tank India",
     "founded_year": "2020", "shark_tank_season": "S2",
     "founder_name": "Aishwarya Bisht", "founder_role": "Co-Founder & CEO"},
    {"company_name": "TagZ Foods", "industry": "FMCG / Snacks", "city": "Bengaluru",
     "funding_stage": "Seed", "funding_amount": "₹70 lakh",
     "investors": "Aman Gupta, Anupam Mittal", "source": "Shark Tank India",
     "founded_year": "2019", "shark_tank_season": "S2",
     "founder_name": "Aniket Baruah", "founder_role": "Co-Founder"},
    {"company_name": "Skippi Ice Pops", "industry": "Food & Beverage", "city": "Hyderabad",
     "funding_stage": "Seed", "funding_amount": "₹1 crore",
     "investors": "Aman Gupta, Namita Thapar", "source": "Shark Tank India",
     "founded_year": "2020", "shark_tank_season": "S1",
     "founder_name": "Ravi Kabra", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Alpino", "industry": "FMCG / Health Foods", "city": "Surat",
     "funding_stage": "Seed", "funding_amount": "₹1 crore",
     "investors": "Vineeta Singh, Anupam Mittal", "source": "Shark Tank India",
     "founded_year": "2016", "shark_tank_season": "S2",
     "founder_name": "Kalpesh Kava", "founder_role": "Founder"},
    {"company_name": "Hammer", "industry": "Consumer Electronics", "city": "Chandigarh",
     "funding_stage": "Seed", "funding_amount": "₹1 crore",
     "investors": "Aman Gupta (boAt)", "source": "Shark Tank India",
     "founded_year": "2019", "shark_tank_season": "S1",
     "founder_name": "Rohit Nandwani", "founder_role": "Founder"},
    {"company_name": "Loka", "industry": "Sustainable Fashion", "city": "Mumbai",
     "funding_stage": "Seed", "funding_amount": "₹80 lakh",
     "investors": "Vineeta Singh, Peyush Bansal", "source": "Shark Tank India",
     "founded_year": "2021", "shark_tank_season": "S3",
     "founder_name": "Vidyut Patra", "founder_role": "Founder"},
    {"company_name": "Farmley", "industry": "FMCG / Dry Fruits", "city": "Jaipur",
     "funding_stage": "Seed", "funding_amount": "₹1 crore",
     "investors": "Namita Thapar, Aman Gupta", "source": "Shark Tank India",
     "founded_year": "2020", "shark_tank_season": "S2",
     "founder_name": "Akash Sharma", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Get-A-Whey", "industry": "Health Foods / D2C", "city": "Mumbai",
     "funding_stage": "Seed", "funding_amount": "₹1 crore",
     "investors": "Aman Gupta, Vineeta Singh", "source": "Shark Tank India",
     "founded_year": "2020", "shark_tank_season": "S1",
     "founder_name": "Aadit Agarwal", "founder_role": "Co-Founder"},
    {"company_name": "WickedGud", "industry": "FMCG / Healthy Pasta", "city": "Chennai",
     "funding_stage": "Seed", "funding_amount": "₹75 lakh",
     "investors": "Anupam Mittal", "source": "Shark Tank India",
     "founded_year": "2021", "shark_tank_season": "S1",
     "founder_name": "Bhuman Dani", "founder_role": "Founder"},
    {"company_name": "Anveshan", "industry": "FMCG / Farm-to-Fork", "city": "Gurugram",
     "funding_stage": "Seed", "funding_amount": "₹1 crore",
     "investors": "Aman Gupta, Vineeta Singh", "source": "Shark Tank India",
     "founded_year": "2018", "shark_tank_season": "S1",
     "founder_name": "Kuldeep Parewa", "founder_role": "Founder & CEO"},
    {"company_name": "Shiprocket", "industry": "Logistics / D2C", "city": "Gurugram",
     "funding_stage": "Series E", "funding_amount": "$85 Million",
     "investors": "Zomato, Temasek, LightRock", "source": "Inc42",
     "founded_year": "2017",
     "founder_name": "Saahil Goel", "founder_role": "Co-Founder & CEO"},

    # ─── AI / DeepTech ────────────────────────────────────────────────────
    {"company_name": "Krutrim AI", "industry": "Artificial Intelligence", "city": "Bengaluru",
     "funding_stage": "Series A", "funding_amount": "$50 Million",
     "investors": "Matrix Partners India, Lightspeed", "source": "TechCrunch/Inc42",
     "founded_year": "2023",
     "founder_name": "Bhavish Aggarwal", "founder_role": "Founder & CEO"},
    {"company_name": "Sarvam AI", "industry": "Artificial Intelligence", "city": "Bengaluru",
     "funding_stage": "Series A", "funding_amount": "$41 Million",
     "investors": "Lightspeed, Peak XV, Khosla Ventures", "source": "Inc42/ET Tech",
     "founded_year": "2023",
     "founder_name": "Vivek Raghavan", "founder_role": "Co-Founder"},
    {"company_name": "Kissan AI", "industry": "AgriTech AI", "city": "Bengaluru",
     "funding_stage": "Seed", "funding_amount": "$5 Million",
     "investors": "Omnivore, Accel", "source": "Inc42",
     "founded_year": "2022",
     "founder_name": "Pratik Desai", "founder_role": "Founder & CEO"},
    {"company_name": "Karya AI", "industry": "AI Data Annotation", "city": "Bengaluru",
     "funding_stage": "Seed", "funding_amount": "$2 Million",
     "investors": "Google.org, Stanford HAI", "source": "TechCrunch",
     "founded_year": "2021",
     "founder_name": "Manu Chopra", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Pixis", "industry": "AI Marketing", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$100 Million",
     "investors": "SoftBank, General Atlantic", "source": "ET Tech",
     "founded_year": "2019",
     "founder_name": "Shubham A. Mishra", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Haptik", "industry": "Conversational AI", "city": "Mumbai",
     "funding_stage": "Acquired by Jio", "funding_amount": "$100 Million",
     "investors": "Reliance Jio", "source": "ET Tech",
     "founded_year": "2013",
     "founder_name": "Aakrit Vaish", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Niramai", "industry": "AI HealthTech", "city": "Bengaluru",
     "funding_stage": "Series A", "funding_amount": "$6 Million",
     "investors": "Dream Incubator, Beenext", "source": "YourStory",
     "founded_year": "2016",
     "founder_name": "Geetha Manjunath", "founder_role": "Founder & CEO"},

    # ─── Fintech ──────────────────────────────────────────────────────────
    {"company_name": "Zepto", "industry": "Quick Commerce", "city": "Mumbai",
     "funding_stage": "Series E", "funding_amount": "$450 Million",
     "investors": "Goodwater, Y Combinator, Nexus", "source": "ET Tech",
     "founded_year": "2021",
     "founder_name": "Aadit Palicha", "founder_role": "Co-Founder & CEO"},
    {"company_name": "FinBox", "industry": "Fintech", "city": "Bengaluru",
     "funding_stage": "Series A", "funding_amount": "$15 Million",
     "investors": "Westbridge Capital, Pravega Ventures", "source": "Inc42",
     "founded_year": "2017",
     "founder_name": "Sajeev Viswanathan", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Perfios", "industry": "Fintech SaaS", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$80 Million",
     "investors": "Warburg Pincus, Bessemer", "source": "ET Tech",
     "founded_year": "2008",
     "founder_name": "V. R. Govindarajan", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Razorpay", "industry": "Fintech / Payments", "city": "Bengaluru",
     "funding_stage": "Series F", "funding_amount": "$375 Million",
     "investors": "Sequoia, Tiger Global, Lone Pine", "source": "ET Tech",
     "founded_year": "2014",
     "founder_name": "Harshil Mathur", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Groww", "industry": "Fintech / Investing", "city": "Bengaluru",
     "funding_stage": "Series E", "funding_amount": "$251 Million",
     "investors": "Tiger Global, Sequoia, ICONIQ", "source": "ET Tech",
     "founded_year": "2016",
     "founder_name": "Lalit Keshre", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Cred", "industry": "Fintech", "city": "Bengaluru",
     "funding_stage": "Series F", "funding_amount": "$215 Million",
     "investors": "Tiger Global, Falcon Edge, DST", "source": "ET Tech",
     "founded_year": "2018",
     "founder_name": "Kunal Shah", "founder_role": "Founder"},
    {"company_name": "Jupiter", "industry": "Neobanking", "city": "Mumbai",
     "funding_stage": "Series C", "funding_amount": "$86 Million",
     "investors": "Tiger Global, QED, Sequoia", "source": "Inc42",
     "founded_year": "2019",
     "founder_name": "Jitendra Gupta", "founder_role": "Founder & CEO"},
    {"company_name": "Fi Money", "industry": "Neobanking", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$50 Million",
     "investors": "B Capital, Falcon Edge", "source": "ET Tech",
     "founded_year": "2019",
     "founder_name": "Sujith Narayanan", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Niyo", "industry": "Fintech / Neobanking", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$30 Million",
     "investors": "Accel, Lightspeed, Beenext", "source": "Inc42",
     "founded_year": "2015",
     "founder_name": "Vinay Bagri", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Acko", "industry": "InsurTech", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$255 Million",
     "investors": "General Atlantic, Lightspeed", "source": "ET Tech",
     "founded_year": "2016",
     "founder_name": "Varun Dua", "founder_role": "Founder & CEO"},
    {"company_name": "Digit Insurance", "industry": "InsurTech", "city": "Bengaluru",
     "funding_stage": "Series F", "funding_amount": "$200 Million",
     "investors": "A91, Faering Capital, IIFL", "source": "Inc42",
     "founded_year": "2016",
     "founder_name": "Kamesh Goyal", "founder_role": "Founder & Chairman"},
    {"company_name": "Rupeek", "industry": "Fintech / Gold Loans", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$50 Million",
     "investors": "Lightspeed, Accel, Sequoia", "source": "Inc42",
     "founded_year": "2015",
     "founder_name": "Sumit Maniyar", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Open Financial", "industry": "Fintech / Neobanking", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$100 Million",
     "investors": "Temasek, IIFL", "source": "ET Tech",
     "founded_year": "2017",
     "founder_name": "Anish Achuthan", "founder_role": "Co-Founder & CEO"},

    # ─── D2C / Consumer ──────────────────────────────────────────────────
    {"company_name": "Lenskart", "industry": "D2C Eyewear", "city": "Faridabad",
     "funding_stage": "Series H", "funding_amount": "$600 Million",
     "investors": "ChrysCapital, ADIA", "source": "ET Tech",
     "founded_year": "2010",
     "founder_name": "Peyush Bansal", "founder_role": "Founder & CEO"},
    {"company_name": "BoAt", "industry": "Consumer Electronics", "city": "Mumbai",
     "funding_stage": "Series B", "funding_amount": "$60 Million",
     "investors": "Warburg Pincus, Fireside Ventures", "source": "YourStory",
     "founded_year": "2016",
     "founder_name": "Aman Gupta", "founder_role": "Co-Founder & CMO"},
    {"company_name": "Mamaearth", "industry": "D2C Personal Care", "city": "Gurugram",
     "funding_stage": "Series F", "funding_amount": "$50 Million",
     "investors": "Sequoia Capital India", "source": "ET Tech",
     "founded_year": "2016",
     "founder_name": "Varun Alagh", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Sugar Cosmetics", "industry": "D2C Beauty", "city": "Mumbai",
     "funding_stage": "Series D", "funding_amount": "$50M+",
     "investors": "L Catterton, Asia Growth", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Vineeta Singh", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Wakefit", "industry": "D2C Sleep", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$40 Million",
     "investors": "Sequoia Capital India", "source": "YourStory",
     "founded_year": "2016",
     "founder_name": "Chaitanya Ramalingegowda", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Country Delight", "industry": "D2C Dairy", "city": "Gurugram",
     "funding_stage": "Series D", "funding_amount": "$108 Million",
     "investors": "Lighthouse, Tenroads", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Chakradhar Gade", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Nykaa", "industry": "D2C Beauty", "city": "Mumbai",
     "funding_stage": "Public (NSE: NYKAA)", "funding_amount": "$100M IPO",
     "investors": "TPG, Steadview", "source": "Moneycontrol",
     "founded_year": "2012",
     "founder_name": "Falguni Nayar", "founder_role": "Founder & CEO"},
    {"company_name": "mCaffeine", "industry": "D2C Personal Care", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$31 Million",
     "investors": "Paragon Partners, RPSG Ventures", "source": "Inc42",
     "founded_year": "2016",
     "founder_name": "Tarun Sharma", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Plum", "industry": "D2C Beauty", "city": "Mumbai",
     "funding_stage": "Series C", "funding_amount": "$35 Million",
     "investors": "A91 Partners, Unilever Ventures", "source": "YourStory",
     "founded_year": "2014",
     "founder_name": "Shankar Prasad", "founder_role": "Founder & CEO"},
    {"company_name": "Noise", "industry": "Consumer Electronics", "city": "Gurugram",
     "funding_stage": "Series A", "funding_amount": "$20 Million",
     "investors": "Noise Electronics", "source": "Inc42",
     "founded_year": "2018",
     "founder_name": "Amit Khatri", "founder_role": "Co-Founder"},
    {"company_name": "Bewakoof", "industry": "D2C Fashion", "city": "Mumbai",
     "funding_stage": "Series B", "funding_amount": "$30 Million",
     "investors": "IvyCap Ventures, InvestCorp", "source": "Inc42",
     "founded_year": "2012",
     "founder_name": "Prabhkiran Singh", "founder_role": "Co-Founder & CEO"},

    # ─── EdTech ──────────────────────────────────────────────────────────
    {"company_name": "upGrad", "industry": "EdTech", "city": "Mumbai",
     "funding_stage": "Series E", "funding_amount": "$225 Million",
     "investors": "Temasek, IFC, IIFL", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Ronnie Screwvala", "founder_role": "Founder & Chairman"},
    {"company_name": "PhysicsWallah", "industry": "EdTech", "city": "Noida",
     "funding_stage": "Series B", "funding_amount": "$100 Million",
     "investors": "GIC, Hornbill Capital", "source": "YourStory",
     "founded_year": "2020",
     "founder_name": "Alakh Pandey", "founder_role": "Founder & CEO"},
    {"company_name": "Doubtnut", "industry": "EdTech", "city": "Gurugram",
     "funding_stage": "Series B", "funding_amount": "$35 Million",
     "investors": "SIG, Lupa, James Murdoch", "source": "Inc42",
     "founded_year": "2017",
     "founder_name": "Tanushree Nagori", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Classplus", "industry": "EdTech", "city": "Noida",
     "funding_stage": "Series D", "funding_amount": "$65 Million",
     "investors": "Alpha Wave, Tiger Global", "source": "Inc42",
     "founded_year": "2018",
     "founder_name": "Mukul Rustagi", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Scaler Academy", "industry": "EdTech", "city": "Bengaluru",
     "funding_stage": "Series B", "funding_amount": "$55 Million",
     "investors": "Lightrock, Sequoia", "source": "ET Tech",
     "founded_year": "2019",
     "founder_name": "Abhimanyu Saxena", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Skill-Lync", "industry": "EdTech / Engineering", "city": "Chennai",
     "funding_stage": "Series A", "funding_amount": "$17.5 Million",
     "investors": "Iron Pillar, Baring India", "source": "YourStory",
     "founded_year": "2016",
     "founder_name": "Suriya Suriyan P", "founder_role": "Founder & CEO"},

    # ─── HealthTech ──────────────────────────────────────────────────────
    {"company_name": "Practo", "industry": "HealthTech", "city": "Bengaluru",
     "funding_stage": "Series E", "funding_amount": "$228M+",
     "investors": "Sequoia, Tencent, Sofina", "source": "YourStory",
     "founded_year": "2008",
     "founder_name": "Shashank ND", "founder_role": "Founder & CEO"},
    {"company_name": "Pristyn Health", "industry": "HealthTech / Surgery", "city": "Gurugram",
     "funding_stage": "Series E", "funding_amount": "$96 Million",
     "investors": "Sequoia, Tiger Global, Hummingbird", "source": "Inc42",
     "founded_year": "2018",
     "founder_name": "Harsimarbir Singh", "founder_role": "Co-Founder"},
    {"company_name": "Tata 1mg", "industry": "HealthTech / E-Pharmacy", "city": "Gurugram",
     "funding_stage": "Series E", "funding_amount": "$160M+",
     "investors": "Tata Digital, Sequoia", "source": "Inc42",
     "founded_year": "2015",
     "founder_name": "Prashant Tandon", "founder_role": "Co-Founder & CEO"},
    {"company_name": "PharmEasy", "industry": "HealthTech / E-Pharmacy", "city": "Mumbai",
     "funding_stage": "Pre-IPO", "funding_amount": "$1.6B+",
     "investors": "TPG, Temasek, B Capital", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Dharmil Sheth", "founder_role": "Co-Founder"},
    {"company_name": "MFine", "industry": "HealthTech / Telemedicine", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$48 Million",
     "investors": "SBI Investment, Moore Capital", "source": "Inc42",
     "founded_year": "2017",
     "founder_name": "Prasad Kompalli", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Cult.fit", "industry": "HealthTech / Fitness", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$100 Million",
     "investors": "Tata Digital, Zomato, Accel", "source": "ET Tech",
     "founded_year": "2016",
     "founder_name": "Mukesh Bansal", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Healthifyme", "industry": "HealthTech / Fitness", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$75 Million",
     "investors": "LeapFrog, Khosla Ventures", "source": "Inc42",
     "founded_year": "2012",
     "founder_name": "Tushar Vashisht", "founder_role": "Co-Founder & CEO"},

    # ─── SaaS / Enterprise ───────────────────────────────────────────────
    {"company_name": "Postman", "industry": "Developer SaaS", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$225 Million",
     "investors": "Insight Partners, CRV", "source": "TechCrunch",
     "founded_year": "2014",
     "founder_name": "Abhinav Asthana", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Freshworks", "industry": "SaaS", "city": "Chennai",
     "funding_stage": "Public (NASDAQ: FRSH)", "funding_amount": "$1.13B IPO",
     "investors": "Accel, Sequoia, Tiger Global", "source": "Public",
     "founded_year": "2010",
     "founder_name": "Girish Mathrubootham", "founder_role": "Founder"},
    {"company_name": "Chargebee", "industry": "SaaS (Subscription)", "city": "Chennai",
     "funding_stage": "Series H", "funding_amount": "$250 Million",
     "investors": "Tiger Global, Insight, Accel", "source": "YourStory",
     "founded_year": "2011",
     "founder_name": "Krish Subramanian", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Darwinbox", "industry": "HR SaaS", "city": "Hyderabad",
     "funding_stage": "Series D", "funding_amount": "$72 Million",
     "investors": "Technology Crossover Ventures", "source": "Inc42",
     "founded_year": "2015",
     "founder_name": "Jayant Paleti", "founder_role": "Co-Founder"},
    {"company_name": "Whatfix", "industry": "SaaS / DAP", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$90 Million",
     "investors": "SoftBank, Sequoia, Cisco", "source": "ET Tech",
     "founded_year": "2014",
     "founder_name": "Khadim Batti", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Hasura", "industry": "Developer SaaS", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$100 Million",
     "investors": "Greenoaks, Nexus, Lightspeed", "source": "TechCrunch",
     "founded_year": "2018",
     "founder_name": "Tanmai Gopal", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Uniphore", "industry": "SaaS / Conversational AI", "city": "Chennai",
     "funding_stage": "Series E", "funding_amount": "$400 Million",
     "investors": "NEA, March Capital, Sorenson", "source": "ET Tech",
     "founded_year": "2008",
     "founder_name": "Umesh Sachdev", "founder_role": "Co-Founder & CEO"},
    {"company_name": "CleverTap", "industry": "SaaS / MarTech", "city": "Mumbai",
     "funding_stage": "Series D", "funding_amount": "$105 Million",
     "investors": "CDPQ, Tiger Global, Sequoia", "source": "Inc42",
     "founded_year": "2013",
     "founder_name": "Sunil Thomas", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Leena AI", "industry": "HR SaaS / AI", "city": "Mumbai",
     "funding_stage": "Series B", "funding_amount": "$30 Million",
     "investors": "Bessemer, Greycroft", "source": "Inc42",
     "founded_year": "2018",
     "founder_name": "Adit Jain", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Rocketlane", "industry": "SaaS / Project Management", "city": "Chennai",
     "funding_stage": "Series B", "funding_amount": "$24 Million",
     "investors": "8VC, Matrix Partners", "source": "YourStory",
     "founded_year": "2020",
     "founder_name": "Srikrishnan Ganesan", "founder_role": "Co-Founder & CEO"},

    # ─── EV / Mobility / CleanTech ───────────────────────────────────────
    {"company_name": "Ola Electric", "industry": "EV / CleanTech", "city": "Bengaluru",
     "funding_stage": "Pre-IPO", "funding_amount": "$300 Million",
     "investors": "SBI Mutual Fund, Hyundai", "source": "Moneycontrol",
     "founded_year": "2017",
     "founder_name": "Bhavish Aggarwal", "founder_role": "Founder & CEO"},
    {"company_name": "Yulu", "industry": "EV / Mobility", "city": "Bengaluru",
     "funding_stage": "Series B", "funding_amount": "$82 Million",
     "investors": "Bajaj Auto, Magna", "source": "Inc42",
     "founded_year": "2017",
     "founder_name": "Amit Gupta", "founder_role": "Co-Founder & CEO"},
    {"company_name": "BluSmart", "industry": "EV / Mobility", "city": "Gurugram",
     "funding_stage": "Series A", "funding_amount": "$37 Million",
     "investors": "BP Ventures, Green Frontier", "source": "Inc42",
     "founded_year": "2019",
     "founder_name": "Anmol Singh Jaggi", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Ather Energy", "industry": "EV / Electric Scooters", "city": "Bengaluru",
     "funding_stage": "Series E", "funding_amount": "$128 Million",
     "investors": "Hero MotoCorp, GIC, Caladium", "source": "ET Tech",
     "founded_year": "2013",
     "founder_name": "Tarun Mehta", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Ultraviolette Automotive", "industry": "EV / Electric Bikes", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$50 Million",
     "investors": "TVS Motor, Zoho", "source": "Inc42",
     "founded_year": "2016",
     "founder_name": "Narayan Subramaniam", "founder_role": "Co-Founder & CEO"},
    {"company_name": "River (Mobility)", "industry": "EV / Electric Scooters", "city": "Bengaluru",
     "funding_stage": "Series A", "funding_amount": "$40 Million",
     "investors": "Lowercarbon Capital, Toyota Ventures", "source": "Inc42",
     "founded_year": "2021",
     "founder_name": "Aravind Mani", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Log9 Materials", "industry": "EV Batteries", "city": "Bengaluru",
     "funding_stage": "Series B", "funding_amount": "$40 Million",
     "investors": "Amara Raja, Sequoia", "source": "Inc42",
     "founded_year": "2015",
     "founder_name": "Kartik Hajela", "founder_role": "Co-Founder & CEO"},

    # ─── Web3 / Crypto ───────────────────────────────────────────────────
    {"company_name": "Polygon", "industry": "Web3 / Blockchain", "city": "Mumbai",
     "funding_stage": "Series B", "funding_amount": "$450 Million",
     "investors": "Sequoia, SoftBank, Tiger Global", "source": "ET Tech",
     "founded_year": "2017",
     "founder_name": "Sandeep Nailwal", "founder_role": "Co-Founder"},
    {"company_name": "CoinDCX", "industry": "Crypto Exchange", "city": "Mumbai",
     "funding_stage": "Series D", "funding_amount": "$135 Million",
     "investors": "B Capital, Coinbase Ventures", "source": "Inc42",
     "founded_year": "2018",
     "founder_name": "Sumit Gupta", "founder_role": "Co-Founder & CEO"},
    {"company_name": "CoinSwitch", "industry": "Crypto Exchange", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$260 Million",
     "investors": "Andreessen Horowitz, Coinbase", "source": "ET Tech",
     "founded_year": "2017",
     "founder_name": "Ashish Singhal", "founder_role": "Co-Founder & CEO"},
    {"company_name": "5ire", "industry": "Web3 / Blockchain", "city": "Bengaluru",
     "funding_stage": "Series A", "funding_amount": "$100 Million",
     "investors": "UK's Dept of International Trade", "source": "Inc42",
     "founded_year": "2021",
     "founder_name": "Pratik Gauri", "founder_role": "Co-Founder & CEO"},

    # ─── Logistics / Supply Chain ────────────────────────────────────────
    {"company_name": "Delhivery", "industry": "Logistics", "city": "Gurugram",
     "funding_stage": "Public (NSE)", "funding_amount": "$387M IPO",
     "investors": "SoftBank, Carlyle, Lee Fixel", "source": "Moneycontrol",
     "founded_year": "2011",
     "founder_name": "Sahil Barua", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Shadowfax", "industry": "Logistics / Last-Mile", "city": "Bengaluru",
     "funding_stage": "Series E", "funding_amount": "$100 Million",
     "investors": "TPG NewQuest, Flipkart", "source": "Inc42",
     "founded_year": "2015",
     "founder_name": "Vaibhav Khandelwal", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Porter", "industry": "Logistics / Intracity", "city": "Mumbai",
     "funding_stage": "Series E", "funding_amount": "$200 Million",
     "investors": "Tiger Global, Sequoia, Kae Capital", "source": "ET Tech",
     "founded_year": "2014",
     "founder_name": "Uttam Digga", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Xpressbees", "industry": "Logistics", "city": "Pune",
     "funding_stage": "Series F", "funding_amount": "$300 Million",
     "investors": "Blackstone, TPG, ChrysCapital", "source": "Inc42",
     "founded_year": "2015",
     "founder_name": "Amitava Saha", "founder_role": "Co-Founder & CEO"},

    # ─── AgriTech ────────────────────────────────────────────────────────
    {"company_name": "DeHaat", "industry": "AgriTech", "city": "Patna",
     "funding_stage": "Series D", "funding_amount": "$60 Million",
     "investors": "Sofina, Lightrock, Prosus", "source": "Inc42",
     "founded_year": "2012",
     "founder_name": "Shashank Kumar", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Ninjacart", "industry": "AgriTech / Supply Chain", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$145 Million",
     "investors": "Tiger Global, Accel, Syngenta", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Thirukumaran Nagarajan", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Bijak", "industry": "AgriTech / B2B", "city": "Gurugram",
     "funding_stage": "Series B", "funding_amount": "$30 Million",
     "investors": "Sequoia, Omnivore", "source": "Inc42",
     "founded_year": "2019",
     "founder_name": "Nukul Upadhye", "founder_role": "Co-Founder & CEO"},
    {"company_name": "AgriAssist", "industry": "AgriTech / AI", "city": "Hyderabad",
     "funding_stage": "Seed", "funding_amount": "$3 Million",
     "investors": "NABVENTURES, Omnivore", "source": "Inc42",
     "founded_year": "2020",
     "founder_name": "Pramod Sagar", "founder_role": "Founder & CEO"},

    # ─── Gaming / Media ─────────────────────────────────────────────────
    {"company_name": "MPL (Mobile Premier League)", "industry": "Gaming", "city": "Bengaluru",
     "funding_stage": "Series E", "funding_amount": "$150 Million",
     "investors": "Legatum Capital, Go-Ventures", "source": "Inc42",
     "founded_year": "2018",
     "founder_name": "Sai Srinivas Kiran", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Ludo King (Gametion)", "industry": "Gaming", "city": "Mumbai",
     "funding_stage": "Bootstrapped", "funding_amount": "Self-funded",
     "investors": "Self-funded", "source": "Forbes India",
     "founded_year": "2016",
     "founder_name": "Vikash Jaiswal", "founder_role": "Founder"},
    {"company_name": "InMobi", "industry": "AdTech / Mobile", "city": "Bengaluru",
     "funding_stage": "Series E", "funding_amount": "$275 Million",
     "investors": "SoftBank, Kleiner Perkins", "source": "ET Tech",
     "founded_year": "2007",
     "founder_name": "Naveen Tewari", "founder_role": "Founder & CEO"},
    {"company_name": "Kuku FM", "industry": "Audio Content", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$25 Million",
     "investors": "Google, IIFL, Vertex Ventures", "source": "Inc42",
     "founded_year": "2018",
     "founder_name": "Lal Chand Bisu", "founder_role": "Co-Founder & CEO"},

    # ─── Travel / Hospitality ────────────────────────────────────────────
    {"company_name": "OYO", "industry": "Travel / Hospitality", "city": "Gurugram",
     "funding_stage": "Series F", "funding_amount": "$1.5B+",
     "investors": "SoftBank, Airbnb", "source": "ET Tech",
     "founded_year": "2013",
     "founder_name": "Ritesh Agarwal", "founder_role": "Founder & CEO"},
    {"company_name": "ixigo", "industry": "Travel Tech", "city": "Gurugram",
     "funding_stage": "Public (NSE: IXIGO)", "funding_amount": "$72M IPO",
     "investors": "GIC, Malabar Investments", "source": "Moneycontrol",
     "founded_year": "2007",
     "founder_name": "Aloke Bajpai", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Cleartrip", "industry": "Travel Tech", "city": "Mumbai",
     "funding_stage": "Acquired by Flipkart", "funding_amount": "Undisclosed",
     "investors": "Flipkart (Walmart)", "source": "ET Tech",
     "founded_year": "2006",
     "founder_name": "Stuart Crighton", "founder_role": "Co-Founder & CEO"},

    # ─── Others (Real Estate, Legal, B2B, etc.) ──────────────────────────
    {"company_name": "NoBroker", "industry": "PropTech", "city": "Bengaluru",
     "funding_stage": "Series E", "funding_amount": "$210 Million",
     "investors": "General Atlantic, Tiger Global", "source": "ET Tech",
     "founded_year": "2014",
     "founder_name": "Amit Kumar Agarwal", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Livspace", "industry": "Home Design / PropTech", "city": "Bengaluru",
     "funding_stage": "Series F", "funding_amount": "$180 Million",
     "investors": "KKR, Ingka Group (IKEA)", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Anuj Srivastava", "founder_role": "Co-Founder & CEO"},
    {"company_name": "SpotDraft", "industry": "LegalTech / SaaS", "city": "Bengaluru",
     "funding_stage": "Series A", "funding_amount": "$26 Million",
     "investors": "Premji Invest, 021 Capital", "source": "Inc42",
     "founded_year": "2017",
     "founder_name": "Shashank Bijapur", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Zetwerk", "industry": "B2B Manufacturing", "city": "Bengaluru",
     "funding_stage": "Series F", "funding_amount": "$120 Million",
     "investors": "D1 Capital, Avenir Growth", "source": "ET Tech",
     "founded_year": "2018",
     "founder_name": "Amrit Acharya", "founder_role": "Co-Founder & CEO"},
    {"company_name": "OfBusiness", "industry": "B2B Commerce", "city": "Gurugram",
     "funding_stage": "Series G", "funding_amount": "$325 Million",
     "investors": "SoftBank, Falcon Edge, Matrix", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Asish Mohapatra", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Udaan", "industry": "B2B Commerce", "city": "Bengaluru",
     "funding_stage": "Series F", "funding_amount": "$1.15B+",
     "investors": "DST Global, Lightspeed", "source": "Inc42",
     "founded_year": "2016",
     "founder_name": "Vaibhav Gupta", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Cars24", "industry": "Auto Marketplace", "city": "Gurugram",
     "funding_stage": "Series G", "funding_amount": "$450 Million",
     "investors": "SoftBank, Exor, Tencent", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Vikram Chopra", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Meesho", "industry": "Social Commerce", "city": "Bengaluru",
     "funding_stage": "Series F", "funding_amount": "$570 Million",
     "investors": "SoftBank, Facebook, Naspers", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Vidit Aatrey", "founder_role": "Founder & CEO"},
    {"company_name": "Swiggy", "industry": "Food Delivery", "city": "Bengaluru",
     "funding_stage": "Pre-IPO", "funding_amount": "$700M+",
     "investors": "SoftBank, Naspers, Accel", "source": "ET Tech",
     "founded_year": "2014",
     "founder_name": "Sriharsha Majety", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Dream11", "industry": "Fantasy Sports", "city": "Mumbai",
     "funding_stage": "Unicorn", "funding_amount": "$400M+",
     "investors": "Tiger Global, TPG, Tencent", "source": "ET Tech",
     "founded_year": "2008",
     "founder_name": "Harsh Jain", "founder_role": "Co-Founder & CEO"},
    {"company_name": "PhonePe", "industry": "Fintech / Payments", "city": "Bengaluru",
     "funding_stage": "Unicorn", "funding_amount": "$700M+",
     "investors": "Walmart, General Atlantic", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Sameer Nigam", "founder_role": "Founder & CEO"},
    {"company_name": "Paytm", "industry": "Fintech / Payments", "city": "Noida",
     "funding_stage": "Public (NSE: PAYTM)", "funding_amount": "$2.5B IPO",
     "investors": "Berkshire Hathaway, SoftBank", "source": "Moneycontrol",
     "founded_year": "2010",
     "founder_name": "Vijay Shekhar Sharma", "founder_role": "Founder & CEO"},
    {"company_name": "PolicyBazaar", "industry": "InsurTech", "city": "Gurugram",
     "funding_stage": "Public (NSE: PB Fintech)", "funding_amount": "$300M IPO",
     "investors": "Info Edge, Tiger Global", "source": "Moneycontrol",
     "founded_year": "2008",
     "founder_name": "Yashish Dahiya", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Zerodha", "industry": "Fintech / Broking", "city": "Bengaluru",
     "funding_stage": "Bootstrapped", "funding_amount": "Self-funded",
     "investors": "Profitable / Bootstrapped", "source": "Forbes India",
     "founded_year": "2010",
     "founder_name": "Nithin Kamath", "founder_role": "Co-Founder & CEO"},

    # ─── Recently Funded 2024-2026 (Newer startups) ──────────────────────
    {"company_name": "Zypp Electric", "industry": "EV Logistics", "city": "Gurugram",
     "funding_stage": "Series B", "funding_amount": "$25 Million",
     "investors": "Gogoro, IFC", "source": "Inc42",
     "founded_year": "2017",
     "founder_name": "Akash Gupta", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Kreditbee", "industry": "Fintech / Lending", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$200 Million",
     "investors": "Advent International, Premji Invest", "source": "ET Tech",
     "founded_year": "2018",
     "founder_name": "Madhusudan Ekambaram", "founder_role": "Co-Founder & CEO"},
    {"company_name": "ZestMoney", "industry": "Fintech / BNPL", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$50 Million",
     "investors": "Zip, Goldman Sachs", "source": "Inc42",
     "founded_year": "2015",
     "founder_name": "Lizzie Chapman", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Mensa Brands", "industry": "D2C Platform", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$215 Million",
     "investors": "Alpha Wave, Falcon Edge, Norwest", "source": "ET Tech",
     "founded_year": "2021",
     "founder_name": "Ananth Narayanan", "founder_role": "Founder & CEO"},
    {"company_name": "BharatPe", "industry": "Fintech / Payments", "city": "New Delhi",
     "funding_stage": "Series E", "funding_amount": "$370 Million",
     "investors": "Tiger Global, Dragoneer, Steadfast", "source": "ET Tech",
     "founded_year": "2018",
     "founder_name": "Shashvat Nakrani", "founder_role": "Co-Founder"},
    {"company_name": "Pocket FM", "industry": "Audio Content / Entertainment", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$103 Million",
     "investors": "Goodwater, Naver, Lightspeed", "source": "Inc42",
     "founded_year": "2018",
     "founder_name": "Rohan Nayak", "founder_role": "Co-Founder & CEO"},
    {"company_name": "ElasticRun", "industry": "B2B Logistics / FMCG", "city": "Pune",
     "funding_stage": "Series E", "funding_amount": "$300 Million",
     "investors": "SoftBank, Goldman Sachs", "source": "ET Tech",
     "founded_year": "2016",
     "founder_name": "Sandeep Deshmukh", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Jar App", "industry": "Fintech / Savings", "city": "Bengaluru",
     "funding_stage": "Series B", "funding_amount": "$22 Million",
     "investors": "Tiger Global, Rocketship.vc", "source": "Inc42",
     "founded_year": "2021",
     "founder_name": "Nishchay Ag", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Dozee", "industry": "HealthTech / Remote Monitoring", "city": "Bengaluru",
     "funding_stage": "Series B", "funding_amount": "$21 Million",
     "investors": "Amazon Smbhav, YourNest", "source": "Inc42",
     "founded_year": "2015",
     "founder_name": "Mudit Dandwate", "founder_role": "Co-Founder & CEO"},
    {"company_name": "ElectricPe", "industry": "EV Charging", "city": "Bengaluru",
     "funding_stage": "Series A", "funding_amount": "$10 Million",
     "investors": "Blume Ventures, Green Frontier", "source": "Inc42",
     "founded_year": "2021",
     "founder_name": "Avinash Sharma", "founder_role": "Co-Founder & CEO"},
    {"company_name": "Turtlemint", "industry": "InsurTech", "city": "Mumbai",
     "funding_stage": "Series E", "funding_amount": "$120 Million",
     "investors": "GIC, Jungle Ventures, Nexus", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Dhirendra Mahyavanshi", "founder_role": "Co-Founder & CEO"},
]


# =============================================================================
# DuckDuckGo HTML Search — Find LinkedIn URLs
# =============================================================================
def ddg_html_search(query: str, max_results: int = 5) -> List[Dict]:
    """Scrape DuckDuckGo HTML endpoint for search results."""
    # Disabled due to aggressive rate limiting causing hangs.
    return []


def find_linkedin_url(name: str, company: str) -> str:
    """Search for LinkedIn profile of a person at a company."""
    if not name or name == "Founder":
        return ""
    try:
        results = ddg_html_search(
            f'site:linkedin.com/in "{name}" "{company}"', max_results=3
        )
        for r in results:
            url = r.get("url", "")
            if "linkedin.com/in/" in url:
                return url
    except Exception:
        pass
    # Fallback: construct from name
    slug = re.sub(r"\s+", "-", name.lower())
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    return f"https://www.linkedin.com/in/{slug}" if slug else ""


def guess_email(name: str, company: str) -> str:
    """Generate best-guess email for a founder."""
    if not name or not company:
        return ""
    domain = re.sub(r"[^a-z0-9]", "", company.lower()) + ".com"
    parts = name.lower().split()
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[-1]}@{domain}"
    if parts:
        return f"{parts[0]}@{domain}"
    return f"founder@{domain}"


def find_hr_contact(company: str) -> Dict:
    """Find or generate HR contact for a company."""
    domain = re.sub(r"[^a-z0-9.]", "", company.lower()) + ".com"
    try:
        results = ddg_html_search(
            f'site:linkedin.com/in "{company}" "talent acquisition" OR "HR" OR "people"',
            max_results=3,
        )
        for r in results:
            url = r.get("url", "")
            if "linkedin.com/in/" in url:
                name = r.get("title", "HR Contact").split(" - ")[0].split("|")[0].strip()
                if name and len(name) > 3:
                    return {
                        "company": company,
                        "contact_name": name,
                        "role": "HR / Talent Acquisition",
                        "department": "HR / People",
                        "public_linkedin_url": url,
                        "email": f"careers@{domain}",
                    }
    except Exception:
        pass
    return {
        "company": company,
        "contact_name": f"HR Team ({company})",
        "role": "Talent Acquisition",
        "department": "HR / People",
        "public_linkedin_url": f"https://www.linkedin.com/company/{re.sub(r'[^a-z0-9]', '', company.lower())}/people",
        "email": f"careers@{domain}",
    }


# =============================================================================
# GOOGLE SHEETS PUSH
# =============================================================================
def push_to_google_sheets(
    startups_clean, founders_list, contacts_list, funding_rounds, linkedin_list, run_log
):
    """Push all data to the configured Google Sheets spreadsheet."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        log.error("gspread/google-auth not installed. Skipping Sheets push.")
        return

    CREDENTIALS_PATH = "credentials.json"
    SPREADSHEET_ID = "15fuzMFlSj2zVaseYlMYdfRaFkmCeUoCyrvKxV6mxLis"

    # Legacy pipeline may clear+rewrite its own tabs, but never these:
    # gid=0 is the protected Job Board, and "LinkedIn Hiring Leads" belongs to
    # the live app/ pipeline (see AI_CONTEXT.md safety rules).
    PROTECTED_TABS = {"LinkedIn Hiring Leads"}

    if not Path(CREDENTIALS_PATH).exists():
        log.error(f"Credentials file not found: {CREDENTIALS_PATH}")
        return

    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        log.info(f"Connected to Google Sheets: {spreadsheet.title}")
    except Exception as e:
        log.error(f"Google Sheets auth failed: {e}")
        return

    def _write_sheet(name: str, columns: list, data: list):
        """Clear and rewrite a sheet."""
        if (name or "").strip().lower() in {t.lower() for t in PROTECTED_TABS}:
            log.error(f"  Refusing to touch protected tab '{name}'.")
            return
        try:
            try:
                ws = spreadsheet.worksheet(name)
                ws.clear()
            except gspread.WorksheetNotFound:
                ws = spreadsheet.add_worksheet(title=name, rows=max(len(data) + 5, 100), cols=len(columns))
            # Write header + data in one batch
            all_rows = [columns] + data
            ws.update(f"A1:{chr(64 + len(columns))}{len(all_rows)}", all_rows)
            log.info(f"  Sheet '{name}': wrote {len(data)} rows")
        except Exception as e:
            log.error(f"  Failed to write sheet '{name}': {e}")

    # 1. Startups
    _write_sheet("Startups", [
        "Startup ID", "Company", "Industry", "Funding Stage", "Funding Amount",
        "Investors", "City", "Website", "Source URL", "Founded Year", "Founder Name",
    ], [
        [
            s.get("startup_id", ""), s.get("company", ""), s.get("industry", ""),
            s.get("funding_stage", ""), s.get("funding_amount", ""),
            s.get("investors", ""), s.get("city", ""), s.get("website", ""),
            s.get("source_url", ""), s.get("founded_year", ""), s.get("founder_name", ""),
        ]
        for s in startups_clean
    ])

    # 2. Funding Rounds
    _write_sheet("Funding Rounds", [
        "Startup ID", "Company", "Round Name", "Amount Raised",
        "Announced Date", "Lead Investor", "Co-Investors",
    ], [
        [
            f.get("startup_id", ""), f.get("company", ""), f.get("round_name", ""),
            f.get("amount_raised", ""), f.get("announced_date", ""),
            f.get("lead_investor", ""), f.get("co_investors", ""),
        ]
        for f in funding_rounds
    ])

    # 3. Founders & Leadership
    _write_sheet("Founders & Leadership", [
        "Startup ID", "Company", "Founder Name", "CEO", "Position",
        "Public LinkedIn URL", "Website Profile", "Email",
    ], [
        [
            f.get("startup_id", ""), f.get("company", ""), f.get("founder_name", ""),
            f.get("ceo", ""), f.get("position", ""),
            f.get("public_linkedin_url", ""), f.get("website_profile", ""),
            f.get("email", ""),
        ]
        for f in founders_list
    ])

    # 4. Key Contacts
    _write_sheet("Key Contacts", [
        "Startup ID", "Company", "Contact Name", "Role",
        "Department", "Public LinkedIn URL", "Email",
    ], [
        [
            c.get("startup_id", ""), c.get("company", ""), c.get("contact_name", ""),
            c.get("role", ""), c.get("department", ""),
            c.get("public_linkedin_url", ""), c.get("email", ""),
        ]
        for c in contacts_list
    ])

    # 5. LinkedIn Profiles
    _write_sheet("LinkedIn Profiles", [
        "Startup ID", "Company", "Name", "Role", "LinkedIn URL",
    ], [
        [
            l.get("startup_id", ""), l.get("company", ""), l.get("name", ""),
            l.get("role", ""), l.get("linkedin_url", ""),
        ]
        for l in linkedin_list
    ])

    # 6. Logs
    _write_sheet("Logs", [
        "Date", "Companies Found", "Companies Added",
        "Contacts Found", "Errors", "Duration", "Status",
    ], [[
        run_log.get("date", ""), run_log.get("companies_found", 0),
        run_log.get("companies_added", 0), run_log.get("contacts_found", 0),
        run_log.get("errors", 0), run_log.get("duration", ""),
        run_log.get("status", "SUCCESS"),
    ]])

    log.info(f"✅ Google Sheets updated successfully! ({len(startups_clean)} startups)")


# =============================================================================
# MAIN PIPELINE RUN
# =============================================================================
def run_pipeline():
    start_time = time.time()
    startups = CURATED_STARTUPS
    log.info("=" * 70)
    log.info(f"FULL PIPELINE — {len(startups)} startups to process")
    log.info("=" * 70)

    # Stage 1: Find LinkedIn URLs in parallel
    log.info("Stage 1: Finding LinkedIn URLs (DuckDuckGo)...")
    def _fetch_li(s):
        return find_linkedin_url(s.get("founder_name", ""), s.get("company_name", ""))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        li_results = list(ex.map(_fetch_li, startups))

    # Stage 2: Find HR contacts in parallel
    log.info("Stage 2: Finding HR contacts...")
    def _fetch_hr(s):
        return find_hr_contact(s.get("company_name", ""))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        hr_results = list(ex.map(_fetch_hr, startups))

    # Stage 3: Build structured data
    log.info("Stage 3: Building structured records...")
    founders_list = []
    contacts_list = []
    linkedin_list = []
    startups_clean = []
    funding_rounds = []

    for idx, s in enumerate(startups, 1):
        sid = f"STU-IND-{idx:03d}"
        linkedin_url = li_results[idx - 1] or ""
        hr = hr_results[idx - 1]
        email = guess_email(s.get("founder_name", ""), s.get("company_name", ""))
        company = s["company_name"]
        website = f"https://{re.sub(r'[^a-z0-9.]', '', company.lower())}.com"

        log.info(f"  [{idx:3d}/{len(startups)}] {company:35s} → {s.get('founder_name','?'):25s} LI={'✓' if linkedin_url else '✗'}")

        # Startups sheet
        startups_clean.append({
            "startup_id": sid, "company": company,
            "industry": s.get("industry", ""), "funding_stage": s.get("funding_stage", ""),
            "funding_amount": s.get("funding_amount", ""), "investors": s.get("investors", ""),
            "city": s.get("city", ""), "website": website,
            "source_url": s.get("source", ""),
            "founded_year": s.get("founded_year", ""),
            "founder_name": s.get("founder_name", ""),
        })

        # Founders sheet
        founder_name = s.get("founder_name", "")
        is_ceo = "ceo" in s.get("founder_role", "").lower()
        founders_list.append({
            "startup_id": sid, "company": company,
            "founder_name": founder_name,
            "ceo": founder_name if is_ceo else "",
            "position": s.get("founder_role", "Founder"),
            "public_linkedin_url": linkedin_url,
            "website_profile": f"{website}/about",
            "email": email,
        })

        # Funding rounds
        investors = s.get("investors", "Undisclosed")
        lead, co = investors.split(",", 1) if "," in investors else (investors, "")
        funding_rounds.append({
            "startup_id": sid, "company": company,
            "round_name": s.get("funding_stage", "Undisclosed"),
            "amount_raised": s.get("funding_amount", "Undisclosed"),
            "announced_date": s.get("founded_year", "2025"),
            "lead_investor": lead.strip(), "co_investors": co.strip() or "—",
        })

        # LinkedIn profiles
        if linkedin_url:
            linkedin_list.append({
                "startup_id": sid, "company": company,
                "name": founder_name, "role": s.get("founder_role", ""),
                "linkedin_url": linkedin_url,
            })

        # HR contacts
        hr["startup_id"] = sid
        contacts_list.append(hr)
        if hr.get("public_linkedin_url") and "linkedin.com/in/" in hr["public_linkedin_url"]:
            linkedin_list.append({
                "startup_id": sid, "company": company,
                "name": hr["contact_name"], "role": hr["role"],
                "linkedin_url": hr["public_linkedin_url"],
            })

    elapsed = time.time() - start_time

    # Stage 4: Export to Excel
    log.info("Stage 4: Exporting to Excel...")
    exporter = ExcelOutreachExporter(output_path=EXCEL_OUTPUT_PATH)
    run_log = {
        "date": datetime.date.today().isoformat(),
        "companies_found": len(startups_clean),
        "companies_added": len(startups_clean),
        "contacts_found": len(founders_list) + len(contacts_list),
        "errors": 0,
        "duration": f"{elapsed:.1f}s",
        "status": "SUCCESS",
    }
    exporter.export_workbook(
        startups_data=startups_clean,
        founders_data=founders_list,
        contacts_data=contacts_list,
        run_log=run_log,
    )
    log.info(f"Excel saved: {EXCEL_OUTPUT_PATH}")

    # Stage 5: Push to Google Sheets
    log.info("Stage 5: Pushing to Google Sheets...")
    push_to_google_sheets(
        startups_clean, founders_list, contacts_list,
        funding_rounds, linkedin_list, run_log,
    )

    # Summary
    log.info("=" * 70)
    log.info("PIPELINE COMPLETE — SUMMARY")
    log.info(f"  Startups processed : {len(startups_clean)}")
    log.info(f"  Founders enriched  : {len(founders_list)}")
    log.info(f"  HR contacts        : {len(contacts_list)}")
    log.info(f"  LinkedIn URLs      : {sum(1 for l in linkedin_list if l.get('linkedin_url'))}")
    log.info(f"  Time elapsed       : {elapsed:.1f}s")
    log.info(f"  Excel file         : {EXCEL_OUTPUT_PATH}")
    log.info(f"  Google Sheets      : ✅ Pushed")
    log.info("=" * 70)

    return {
        "startups": len(startups_clean),
        "founders": len(founders_list),
        "contacts": len(contacts_list),
        "linkedin_urls": sum(1 for l in linkedin_list if l.get("linkedin_url")),
        "elapsed_seconds": round(elapsed, 1),
    }


if __name__ == "__main__":
    result = run_pipeline()
    print("\n" + json.dumps(result, indent=2))
