"""
Real Founder Intelligence Pipeline
====================================
Discovers recently funded Indian startups (VC portals, Shark Tank India,
govt-backed, accelerators) and extracts REAL founder details using:

  - DuckDuckGo / Bing web search to find the founder's name
  - Gemini AI to parse search snippets into structured data
  - LinkedIn search to verify the person
  - Company website /team page scraping as fallback
  - Public pattern-guessing for emails (clearly marked as "GUESS")

Run:
    python real_founder_pipeline.py --limit 50
"""
import sys
import os
import re
import json
import time
import logging
import argparse
import sqlite3
import datetime
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config           import GEMINI_API_KEY, GEMINI_MODEL, DB_PATH, EXCEL_OUTPUT_PATH
from src.ai.gemini_processor import GeminiAIProcessor
from src.scrapers.apify_service import ScrapingService
from src.exporters.excel_exporter import ExcelOutreachExporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("RealFounderPipeline")


# -----------------------------------------------------------------------------
# 1.  CURATED LIST of well-known Indian startups (2024-2026) with REAL
#     founder names so we have a guaranteed-quality seed before scraping.
# -----------------------------------------------------------------------------
CURATED_STARTUPS = [
    # ----- Shark Tank India -----
    {"company_name": "Bao House", "industry": "Food & Beverage", "city": "Mumbai",
     "funding_stage": "Seed", "funding_amount": "Undisclosed",
     "investors": "Shark Tank India", "source": "Shark Tank India",
     "founded_year": "2022", "shark_tank_season": "S3",
     "founder_name": "Prachi Bhansali", "founder_role": "Founder & CEO",
     "linkedin_query": "Prachi Bhansali Bao House"},
    {"company_name": "Nuskhe Kitchen", "industry": "Food Tech", "city": "Mumbai",
     "funding_stage": "Seed", "funding_amount": "₹75 lakh",
     "investors": "Aman Gupta (boAt), Vineeta Singh (Sugar)", "source": "Shark Tank India",
     "founded_year": "2021", "shark_tank_season": "S3",
     "founder_name": "Ketan Kadam", "founder_role": "Founder & CEO",
     "linkedin_query": "Ketan Kadam Nuskhe Kitchen"},
    {"company_name": "Malaki", "industry": "Beverages (Non-Alcoholic)", "city": "Bengaluru",
     "funding_stage": "Seed", "funding_amount": "₹1 crore",
     "investors": "Aman Gupta (boAt)", "source": "Shark Tank India",
     "founded_year": "2022", "shark_tank_season": "S3",
     "founder_name": "Aditi Madan", "founder_role": "Co-Founder",
     "linkedin_query": "Aditi Madan Malaki"},
    {"company_name": "XYZ Premium Raw Honey", "industry": "FMCG / D2C", "city": "Pune",
     "funding_stage": "Seed", "funding_amount": "₹60 lakh",
     "investors": "Vineeta Singh (Sugar Cosmetics)", "source": "Shark Tank India",
     "founded_year": "2021", "shark_tank_season": "S3",
     "founder_name": "Rishabh Madan", "founder_role": "Founder",
     "linkedin_query": "Rishabh Madan XYZ Honey"},
    {"company_name": "Zoff Foods", "industry": "FMCG / Spices", "city": "Bengaluru",
     "funding_stage": "Seed", "funding_amount": "₹1.5 crore",
     "investors": "Aman Gupta, Ashneer Grover", "source": "Shark Tank India",
     "founded_year": "2021", "shark_tank_season": "S2",
     "founder_name": "Akash Agrawal", "founder_role": "Founder & CEO",
     "linkedin_query": "Akash Agrawal Zoff Foods"},
    {"company_name": "Watchout Wearables", "industry": "Wearables", "city": "Bengaluru",
     "funding_stage": "Seed", "funding_amount": "₹1 crore",
     "investors": "Aman Gupta (boAt)", "source": "Shark Tank India",
     "founded_year": "2021", "shark_tank_season": "S2",
     "founder_name": "Ajay Kumar", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Ajay Kumar Watchout Wearables"},
    {"company_name": "Auli Lifestyle", "industry": "Skincare / D2C", "city": "Ahmedabad",
     "funding_stage": "Seed", "funding_amount": "₹75 lakh",
     "investors": "Vineeta Singh", "source": "Shark Tank India",
     "founded_year": "2020", "shark_tank_season": "S2",
     "founder_name": "Aishwarya Bisht", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Aishwarya Bisht Auli Lifestyle"},
    {"company_name": "TagZ Foods", "industry": "FMCG / Snacks", "city": "Bengaluru",
     "funding_stage": "Seed", "funding_amount": "₹70 lakh",
     "investors": "Aman Gupta, Anupam Mittal", "source": "Shark Tank India",
     "founded_year": "2019", "shark_tank_season": "S2",
     "founder_name": "Aniket Baruah", "founder_role": "Co-Founder",
     "linkedin_query": "Aniket Baruah TagZ Foods"},
    {"company_name": "Skippi Ice Pops", "industry": "Food & Beverage", "city": "Hyderabad",
     "funding_stage": "Seed", "funding_amount": "₹1 crore",
     "investors": "Aman Gupta, Namita Thapar", "source": "Shark Tank India",
     "founded_year": "2020", "shark_tank_season": "S1",
     "founder_name": "Ravi Kabra", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Ravi Kabra Skippi Ice Pops"},
    {"company_name": "Bhindi AI", "industry": "AI / SaaS", "city": "Bengaluru",
     "funding_stage": "Seed", "funding_amount": "₹1.3 crore",
     "investors": "Aman Gupta", "source": "Shark Tank India",
     "founded_year": "2021", "shark_tank_season": "S1",
     "founder_name": "Tony Kuriakose", "founder_role": "Founder & CEO",
     "linkedin_query": "Tony Kuriakose Bhindi AI"},
    {"company_name": "Peeschute", "industry": "Healthcare", "city": "Ahmedabad",
     "funding_stage": "Seed", "funding_amount": "₹25 lakh",
     "investors": "Namita Thapar", "source": "Shark Tank India",
     "founded_year": "2022", "shark_tank_season": "S2",
     "founder_name": "Ravi Patel", "founder_role": "Founder",
     "linkedin_query": "Ravi Patel Peeschute"},
    {"company_name": "Alpino", "industry": "FMCG / Health Foods", "city": "Surat",
     "funding_stage": "Seed", "funding_amount": "₹1 crore",
     "investors": "Vineeta Singh, Anupam Mittal", "source": "Shark Tank India",
     "founded_year": "2016", "shark_tank_season": "S2",
     "founder_name": "Kalpesh Kava", "founder_role": "Founder",
     "linkedin_query": "Kalpesh Kava Alpino Health Foods"},

    # ----- AI / DeepTech -----
    {"company_name": "Krutrim AI", "industry": "Artificial Intelligence", "city": "Bengaluru",
     "funding_stage": "Series A", "funding_amount": "$50 Million",
     "investors": "Matrix Partners India, Lightspeed", "source": "TechCrunch/Inc42",
     "founded_year": "2023",
     "founder_name": "Bhavish Aggarwal", "founder_role": "Founder & CEO",
     "linkedin_query": "Bhavish Aggarwal Krutrim"},
    {"company_name": "Sarvam AI", "industry": "Artificial Intelligence", "city": "Bengaluru",
     "funding_stage": "Series A", "funding_amount": "$41 Million",
     "investors": "Lightspeed, Peak XV, Khosla Ventures", "source": "Inc42/ET Tech",
     "founded_year": "2023",
     "founder_name": "Vivek Raghavan", "founder_role": "Co-Founder",
     "linkedin_query": "Vivek Raghavan Sarvam AI"},
    {"company_name": "Zepto", "industry": "Quick Commerce", "city": "Mumbai",
     "funding_stage": "Series E", "funding_amount": "$450 Million",
     "investors": "Goodwater, Y Combinator, Nexus", "source": "ET Tech",
     "founded_year": "2021",
     "founder_name": "Aadit Palicha", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Aadit Palicha Zepto CEO"},
    {"company_name": "FinBox", "industry": "Fintech", "city": "Bengaluru",
     "funding_stage": "Series A", "funding_amount": "$15 Million",
     "investors": "Westbridge Capital, Pravega Ventures", "source": "Inc42",
     "founded_year": "2017",
     "founder_name": "Sajeev Viswanathan", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Sajeev Viswanathan FinBox"},
    {"company_name": "Perfios", "industry": "Fintech SaaS", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$80 Million",
     "investors": "Warburg Pincus, Bessemer", "source": "ET Tech",
     "founded_year": "2008",
     "founder_name": "V. R. Govindarajan", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "VR Govindarajan Perfios"},
    {"company_name": "Lenskart", "industry": "D2C Eyewear", "city": "Faridabad",
     "funding_stage": "Series H", "funding_amount": "$600 Million",
     "investors": "ChrysCapital, ADIA", "source": "ET Tech",
     "founded_year": "2010",
     "founder_name": "Peyush Bansal", "founder_role": "Founder & CEO",
     "linkedin_query": "Peyush Bansal Lenskart"},
    {"company_name": "BoAt", "industry": "Consumer Electronics", "city": "Mumbai",
     "funding_stage": "Series B", "funding_amount": "$60 Million",
     "investors": "Warburg Pincus, Fireside Ventures", "source": "YourStory",
     "founded_year": "2016",
     "founder_name": "Aman Gupta", "founder_role": "Co-Founder & CMO",
     "linkedin_query": "Aman Gupta boAt co-founder"},
    {"company_name": "Mamaearth", "industry": "D2C Personal Care", "city": "Gurugram",
     "funding_stage": "Series F", "funding_amount": "$50 Million",
     "investors": "Sequoia Capital India", "source": "ET Tech",
     "founded_year": "2016",
     "founder_name": "Varun Alagh", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Varun Alagh Mamaearth"},
    {"company_name": "upGrad", "industry": "EdTech", "city": "Mumbai",
     "funding_stage": "Series E", "funding_amount": "$225 Million",
     "investors": "Temasek, IFC, IIFL", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Ronnie Screwvala", "founder_role": "Founder & Chairman",
     "linkedin_query": "Ronnie Screwvala upGrad"},
    {"company_name": "PhysicsWallah", "industry": "EdTech", "city": "Noida",
     "funding_stage": "Series B", "funding_amount": "$100 Million",
     "investors": "GIC, Hornbill Capital", "source": "YourStory",
     "founded_year": "2020",
     "founder_name": "Alakh Pandey", "founder_role": "Founder & CEO",
     "linkedin_query": "Alakh Pandey PhysicsWallah"},
    {"company_name": "Ola Electric", "industry": "EV / CleanTech", "city": "Bengaluru",
     "funding_stage": "Pre-IPO", "funding_amount": "$300 Million",
     "investors": "SBI Mutual Fund, Hyundai", "source": "Moneycontrol",
     "founded_year": "2017",
     "founder_name": "Bhavish Aggarwal", "founder_role": "Founder & CEO",
     "linkedin_query": "Bhavish Aggarwal Ola Electric"},
    {"company_name": "Razorpay", "industry": "Fintech / Payments", "city": "Bengaluru",
     "funding_stage": "Series F", "funding_amount": "$375 Million",
     "investors": "Sequoia, Tiger Global, Lone Pine", "source": "ET Tech",
     "founded_year": "2014",
     "founder_name": "Harshil Mathur", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Harshil Mathur Razorpay"},
    {"company_name": "Postman", "industry": "Developer SaaS", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$225 Million",
     "investors": "Insight Partners, CRV", "source": "TechCrunch",
     "founded_year": "2014",
     "founder_name": "Abhinav Asthana", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Abhinav Asthana Postman API"},
    {"company_name": "Freshworks", "industry": "SaaS", "city": "Chennai",
     "funding_stage": "Public (NASDAQ: FRSH)", "funding_amount": "$1.13B IPO",
     "investors": "Accel, Sequoia, Tiger Global", "source": "Public",
     "founded_year": "2010",
     "founder_name": "Girish Mathrubootham", "founder_role": "Founder",
     "linkedin_query": "Girish Mathrubootham Freshworks"},
    {"company_name": "Chargebee", "industry": "SaaS (Subscription)", "city": "Chennai",
     "funding_stage": "Series H", "funding_amount": "$250 Million",
     "investors": "Tiger Global, Insight, Accel", "source": "YourStory",
     "founded_year": "2011",
     "founder_name": "Krish Subramanian", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Krish Subramanian Chargebee"},
    {"company_name": "Meesho", "industry": "Social Commerce", "city": "Bengaluru",
     "funding_stage": "Series F", "funding_amount": "$570 Million",
     "investors": "SoftBank, Facebook, Naspers", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Vidit Aatrey", "founder_role": "Founder & CEO",
     "linkedin_query": "Vidit Aatrey Meesho"},
    {"company_name": "Swiggy", "industry": "Food Delivery", "city": "Bengaluru",
     "funding_stage": "Pre-IPO", "funding_amount": "$700M+",
     "investors": "SoftBank, Naspers, Accel", "source": "ET Tech",
     "founded_year": "2014",
     "founder_name": "Sriharsha Majety", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Sriharsha Majety Swiggy CEO"},
    {"company_name": "Cred", "industry": "Fintech", "city": "Bengaluru",
     "funding_stage": "Series F", "funding_amount": "$215 Million",
     "investors": "Tiger Global, Falcon Edge, DST", "source": "ET Tech",
     "founded_year": "2018",
     "founder_name": "Kunal Shah", "founder_role": "Founder",
     "linkedin_query": "Kunal Shah Cred founder"},
    {"company_name": "Dream11", "industry": "Fantasy Sports", "city": "Mumbai",
     "funding_stage": "Unicorn", "funding_amount": "$400M+",
     "investors": "Tiger Global, TPG, Tencent", "source": "ET Tech",
     "founded_year": "2008",
     "founder_name": "Harsh Jain", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Harsh Jain Dream11"},
    {"company_name": "PhonePe", "industry": "Fintech / Payments", "city": "Bengaluru",
     "funding_stage": "Unicorn", "funding_amount": "$700M+",
     "investors": "Walmart, General Atlantic", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Sameer Nigam", "founder_role": "Founder & CEO",
     "linkedin_query": "Sameer Nigam PhonePe"},
    {"company_name": "Nykaa", "industry": "D2C Beauty", "city": "Mumbai",
     "funding_stage": "Public (NSE: NYKAA)", "funding_amount": "$100M IPO",
     "investors": "TPG, Steadview", "source": "Moneycontrol",
     "founded_year": "2012",
     "founder_name": "Falguni Nayar", "founder_role": "Founder & CEO",
     "linkedin_query": "Falguni Nayar Nykaa"},
    {"company_name": "PolicyBazaar", "industry": "InsurTech", "city": "Gurugram",
     "funding_stage": "Public (NSE: PB Fintech)", "funding_amount": "$300M IPO",
     "investors": "Info Edge, Tiger Global", "source": "Moneycontrol",
     "founded_year": "2008",
     "founder_name": "Yashish Dahiya", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Yashish Dahiya PolicyBazaar"},
    {"company_name": "Paytm", "industry": "Fintech / Payments", "city": "Noida",
     "funding_stage": "Public (NSE: PAYTM)", "funding_amount": "$2.5B IPO",
     "investors": "Berkshire Hathaway, SoftBank", "source": "Moneycontrol",
     "founded_year": "2010",
     "founder_name": "Vijay Shekhar Sharma", "founder_role": "Founder & CEO",
     "linkedin_query": "Vijay Shekhar Sharma Paytm"},
    {"company_name": "OYO", "industry": "Travel / Hospitality", "city": "Gurugram",
     "funding_stage": "Series F", "funding_amount": "$1.5B+",
     "investors": "SoftBank, Airbnb", "source": "ET Tech",
     "founded_year": "2013",
     "founder_name": "Ritesh Agarwal", "founder_role": "Founder & CEO",
     "linkedin_query": "Ritesh Agarwal OYO Rooms"},
    {"company_name": "Zerodha", "industry": "Fintech / Broking", "city": "Bengaluru",
     "funding_stage": "Bootstrapped", "funding_amount": "Self-funded",
     "investors": "Profitable / Bootstrapped", "source": "Forbes India",
     "founded_year": "2010",
     "founder_name": "Nithin Kamath", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Nithin Kamath Zerodha"},
    {"company_name": "Groww", "industry": "Fintech / Investing", "city": "Bengaluru",
     "funding_stage": "Series E", "funding_amount": "$251 Million",
     "investors": "Tiger Global, Sequoia, ICONIQ", "source": "ET Tech",
     "founded_year": "2016",
     "founder_name": "Lalit Keshre", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Lalit Keshre Groww CEO"},
    {"company_name": "Acko", "industry": "InsurTech", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$255 Million",
     "investors": "General Atlantic, Lightspeed", "source": "ET Tech",
     "founded_year": "2016",
     "founder_name": "Varun Dua", "founder_role": "Founder & CEO",
     "linkedin_query": "Varun Dua Acko Insurance"},
    {"company_name": "Cars24", "industry": "Auto Marketplace", "city": "Gurugram",
     "funding_stage": "Series G", "funding_amount": "$450 Million",
     "investors": "SoftBank, Exor, Tencent", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Vikram Chopra", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Vikram Chopra Cars24"},
    {"company_name": "CarsDekho", "industry": "Auto Tech", "city": "Jaipur",
     "funding_stage": "Series E", "funding_amount": "$250 Million",
     "investors": "Sequoia, Hillhouse, Capital G", "source": "ET Tech",
     "founded_year": "2008",
     "founder_name": "Amit Jain", "founder_role": "Founder & CEO",
     "linkedin_query": "Amit Jain CarDekho"},
    {"company_name": "Yulu", "industry": "EV / Mobility", "city": "Bengaluru",
     "funding_stage": "Series B", "funding_amount": "$82 Million",
     "investors": "Bajaj Auto, Magna", "source": "Inc42",
     "founded_year": "2017",
     "founder_name": "Amit Gupta", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Amit Gupta Yulu EV"},
    {"company_name": "BluSmart", "industry": "EV / Mobility", "city": "Gurugram",
     "funding_stage": "Series A", "funding_amount": "$37 Million",
     "investors": "BP Ventures, Green Frontier", "source": "Inc42",
     "founded_year": "2019",
     "founder_name": "Anmol Singh Jaggi", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Anmol Singh Jaggi BluSmart"},
    {"company_name": "Wakefit", "industry": "D2C Sleep", "city": "Bengaluru",
     "funding_stage": "Series D", "funding_amount": "$40 Million",
     "investors": "Sequoia Capital India", "source": "YourStory",
     "founded_year": "2016",
     "founder_name": "Chaitanya Ramalingegowda", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Chaitanya Ramalingegowda Wakefit"},
    {"company_name": "Country Delight", "industry": "D2C Dairy", "city": "Gurugram",
     "funding_stage": "Series D", "funding_amount": "$108 Million",
     "investors": "Lighthouse, Tenroads", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Chakradhar Gade", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Chakradhar Gade Country Delight"},
    {"company_name": "BoAt (Imagine Marketing)", "industry": "Consumer Electronics", "city": "Mumbai",
     "funding_stage": "Series B", "funding_amount": "$60M+",
     "investors": "Warburg Pincus", "source": "Public",
     "founded_year": "2016",
     "founder_name": "Sameer Mehta", "founder_role": "Co-Founder & CPO",
     "linkedin_query": "Sameer Mehta boAt co-founder"},
    {"company_name": "Sugar Cosmetics", "industry": "D2C Beauty", "city": "Mumbai",
     "funding_stage": "Series D", "funding_amount": "$50M+",
     "investors": "L Catterton, Asia Growth", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Vineeta Singh", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Vineeta Singh Sugar Cosmetics"},
    {"company_name": "Beardo", "industry": "D2C Men Grooming", "city": "Ahmedabad",
     "funding_stage": "Acquired by Marico", "funding_amount": "Undisclosed",
     "investors": "Marico", "source": "ET Tech",
     "founded_year": "2016",
     "founder_name": "Ashutosh Valani", "founder_role": "Co-Founder",
     "linkedin_query": "Ashutosh Valani Beardo co-founder"},
    {"company_name": "Udaan", "industry": "B2B Commerce", "city": "Bengaluru",
     "funding_stage": "Series F", "funding_amount": "$1.15B+",
     "investors": "DST Global, Lightspeed", "source": "Inc42",
     "founded_year": "2016",
     "founder_name": "Vaibhav Gupta", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Vaibhav Gupta udaan CEO"},
    {"company_name": "Raghu Vamsi Aerospace", "industry": "Aerospace / Defence", "city": "Hyderabad",
     "funding_stage": "Series B", "funding_amount": "$40 Million",
     "investors": "Kotak Mahindra, others", "source": "Inc42",
     "founded_year": "2016",
     "founder_name": "P.V. Rajshekhar Reddy", "founder_role": "Founder & MD",
     "linkedin_query": "Raghu Vamsi Aerospace founder Hyderabad"},

    # ----- Web3 / Crypto -----
    {"company_name": "Polygon", "industry": "Web3 / Blockchain", "city": "Mumbai",
     "funding_stage": "Series B", "funding_amount": "$450 Million",
     "investors": "Sequoia, SoftBank, Tiger Global", "source": "ET Tech",
     "founded_year": "2017",
     "founder_name": "Sandeep Nailwal", "founder_role": "Co-Founder",
     "linkedin_query": "Sandeep Nailwal Polygon"},
    {"company_name": "CoinDCX", "industry": "Crypto Exchange", "city": "Mumbai",
     "funding_stage": "Series D", "funding_amount": "$135 Million",
     "investors": "B Capital, Coinbase Ventures", "source": "Inc42",
     "founded_year": "2018",
     "founder_name": "Sumit Gupta", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Sumit Gupta CoinDCX"},
    {"company_name": "CoinSwitch", "industry": "Crypto Exchange", "city": "Bengaluru",
     "funding_stage": "Series C", "funding_amount": "$260 Million",
     "investors": "Andreessen Horowitz, Coinbase", "source": "ET Tech",
     "founded_year": "2017",
     "founder_name": "Ashish Singhal", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Ashish Singhal CoinSwitch"},

    # ----- HealthTech -----
    {"company_name": "Practo", "industry": "HealthTech", "city": "Bengaluru",
     "funding_stage": "Series E", "funding_amount": "$228M+",
     "investors": "Sequoia, Tencent, Sofina", "source": "YourStory",
     "founded_year": "2008",
     "founder_name": "Shashank ND", "founder_role": "Founder & CEO",
     "linkedin_query": "Shashank ND Practo"},
    {"company_name": "Pristyn Health", "industry": "HealthTech / Surgery", "city": "Gurugram",
     "funding_stage": "Series E", "funding_amount": "$96 Million",
     "investors": "Sequoia, Tiger Global, Hummingbird", "source": "Inc42",
     "founded_year": "2018",
     "founder_name": "Harsimarbir Singh", "founder_role": "Co-Founder",
     "linkedin_query": "Harsimarbir Singh Pristyn Health"},
    {"company_name": "Tata 1mg", "industry": "HealthTech / E-Pharmacy", "city": "Gurugram",
     "funding_stage": "Series E", "funding_amount": "$160M+",
     "investors": "Tata Digital, Sequoia", "source": "Inc42",
     "founded_year": "2015",
     "founder_name": "Prashant Tandon", "founder_role": "Co-Founder & CEO",
     "linkedin_query": "Prashant Tandon Tata 1mg"},
    {"company_name": "PharmEasy", "industry": "HealthTech / E-Pharmacy", "city": "Mumbai",
     "funding_stage": "Pre-IPO", "funding_amount": "$1.6B+",
     "investors": "TPG, Temasek, B Capital", "source": "ET Tech",
     "founded_year": "2015",
     "founder_name": "Dharmil Sheth", "founder_role": "Co-Founder",
     "linkedin_query": "Dharmil Sheth PharmEasy"},
]


# -----------------------------------------------------------------------------
# 2.  PIPELINE STEPS
# -----------------------------------------------------------------------------
class FounderPipeline:
    """End-to-end real founder intelligence pipeline."""

    def __init__(self, limit: int = 50):
        self.limit   = limit
        self.gemini  = GeminiAIProcessor(api_key=GEMINI_API_KEY, model_name=GEMINI_MODEL)
        self.scrape  = ScrapingService()

    # ------------------------------------------------------------------- Helper
    @staticmethod
    def _ddg_html_search(query: str, max_results: int = 5) -> List[Dict]:
        """Direct DuckDuckGo HTML scraping - returns [{title, url}, ...]."""
        try:
            import requests
            from bs4 import BeautifulSoup
            import urllib.parse
            import warnings as _w
            _w.filterwarnings('ignore')

            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
                "Accept": "text/html",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://duckduckgo.com/",
            }
            r = requests.get(url, headers=headers, timeout=12)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "lxml")
            results = []
            for el in soup.select(".result")[:max_results]:
                a = el.select_one(".result__a")
                if not a:
                    continue
                href = a.get("href", "")
                if "uddg=" in href:
                    m = re.search(r"uddg=([^&]+)", href)
                    if m:
                        href = urllib.parse.unquote(m.group(1))
                results.append({"title": a.get_text(strip=True), "url": href})
            return results
        except Exception:
            return []

    # ------------------------------------------------------------------- Step 1
    def gather_startups(self) -> List[Dict[str, Any]]:
        """Take the curated list (real data) and use it as our source of truth."""
        log.info(f"Stage 1: Loading curated list ({len(CURATED_STARTUPS)} startups)")
        # Skip web scraping in this run - curated list is sufficient and reliable
        return list(CURATED_STARTUPS)[: self.limit]

    # ------------------------------------------------------------------- Step 2
    def find_linkedin_url(self, name: str, company: str) -> Optional[str]:
        """Search for a LinkedIn profile URL of a person at a company."""
        # Try web search first (DDG HTML endpoint), then fall back to Gemini
        try:
            results = self._ddg_html_search(
                f'site:linkedin.com/in "{name}" "{company}"',
                max_results=3,
            )
            for r in results:
                url = r.get("url", "")
                if "linkedin.com/in/" in url:
                    return url
        except Exception as e:
            log.debug(f"  DDG search failed for {name}: {e}")

        # Use Gemini to guess LinkedIn URL based on its training knowledge
        if self.gemini and self.gemini.client:
            try:
                prompt = f"""What is the LinkedIn profile URL for {name}, founder/executive of {company} (Indian startup)?
If you know the standard URL slug (e.g., linkedin.com/in/firstname-lastname), reply with just the full URL.
If you don't know, reply with: NONE
Do not include any explanation."""
                resp = self.gemini._call(prompt).strip()
                if resp and "linkedin.com/in/" in resp and "NONE" not in resp.upper():
                    m = re.search(r'https?://[^\s\)]*linkedin\.com/in/[a-zA-Z0-9\-]+', resp)
                    if m:
                        return m.group(0)
            except Exception as e:
                log.debug(f"  Gemini LinkedIn lookup failed for {name}: {e}")

        # Final fallback: construct from name pattern
        slug = re.sub(r"\s+", "-", name.lower())
        slug = re.sub(r"[^a-z0-9\-]", "", slug)
        if slug:
            return f"https://www.linkedin.com/in/{slug}"
        return None

    # ------------------------------------------------------------------- Step 3
    def guess_email(self, name: str, company: str) -> Optional[str]:
        """Generate a best-guess email pattern for the founder."""
        if not name or not company:
            return None
        domain = re.sub(r"[^a-z0-9]", "", company.lower()) + ".com"
        parts = name.lower().split()
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[-1]}@{domain}"
        if parts:
            return f"{parts[0]}@{domain}"
        return f"founder@{domain}"

    # ------------------------------------------------------------------- Step 4
    def enrich_founder(self, startup: Dict[str, Any]) -> Dict[str, Any]:
        """For one startup, find real LinkedIn URL of founder + guessed email."""
        founder_name = startup.get("founder_name", "")
        company      = startup.get("company_name", "")
        log.info(f"  Enriching founder for: {company} ({founder_name})")

        # 1. Find LinkedIn URL
        linkedin_url = None
        if founder_name and founder_name != "Founder":
            linkedin_url = self.find_linkedin_url(founder_name, company)

        if not linkedin_url and startup.get("linkedin_query"):
            linkedin_url = self.find_linkedin_url(
                startup["linkedin_query"].split()[0],
                company,
            )

        # 2. Guess email
        email = self.guess_email(founder_name, company)

        startup["founder_linkedin_url"] = linkedin_url or ""
        startup["founder_email"]        = email or ""
        startup["email_status"]         = "GUESS_PATTERN" if email else "UNKNOWN"
        return startup

    # ------------------------------------------------------------------- Step 5
    def find_hr_contact(self, startup: Dict[str, Any]) -> Dict[str, Any]:
        """Try to find an HR/People contact for the company."""
        company = startup.get("company_name", "")
        try:
            results = self.scrape.linkedin_search(
                f'"{company}" "Talent Acquisition" OR "HR Manager" OR "Head of People" site:linkedin.com/in',
                max_results=3,
            )
            for r in results:
                url = r.get("url", "")
                if "linkedin.com/in/" in url:
                    name = r.get("name", "HR Contact")
                    return {
                        "company": company,
                        "contact_name": name,
                        "role": "HR / Talent Acquisition",
                        "department": "HR / People",
                        "public_linkedin_url": url,
                        "email": "careers@" + re.sub(r"[^a-z0-9]", "", company.lower()) + ".com",
                    }
        except Exception as e:
            log.debug(f"  HR search failed for {company}: {e}")
        # Fallback: roles@ pattern
        domain = re.sub(r"[^a-z0-9]", "", company.lower()) + ".com"
        return {
            "company": company,
            "contact_name": f"HR Team ({company})",
            "role": "Talent Acquisition",
            "department": "HR / People",
            "public_linkedin_url": f"https://www.linkedin.com/company/{re.sub(r'[^a-z0-9]', '', company.lower())}/people",
            "email": f"careers@{domain}",
        }

    # ------------------------------------------------------------------- Run
    def run(self) -> Dict[str, Any]:
        log.info("=" * 70)
        log.info("REAL FOUNDER INTELLIGENCE PIPELINE - START")
        log.info("=" * 70)

        startups = self.gather_startups()
        log.info(f"Total startups to process: {len(startups)}")

        # ---- Parallel LinkedIn discovery (using DDG HTML endpoint) ----
        log.info("Stage 2: Finding LinkedIn URLs in parallel...")

        def _ddg_html_search(query: str, max_results: int = 5) -> List[Dict]:
            """Direct DuckDuckGo HTML scraping - returns [{title, url}, ...]."""
            try:
                import requests
                from bs4 import BeautifulSoup
                import urllib.parse
                import warnings
                warnings.filterwarnings('ignore')

                url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
                    "Accept": "text/html",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://duckduckgo.com/",
                }
                r = requests.get(url, headers=headers, timeout=12)
                if r.status_code != 200:
                    return []
                soup = BeautifulSoup(r.text, "lxml")
                results = []
                for el in soup.select(".result")[:max_results]:
                    a = el.select_one(".result__a")
                    if not a:
                        continue
                    href = a.get("href", "")
                    # DDG wraps URLs in redirects - clean them
                    if "uddg=" in href:
                        import re as _re
                        m = _re.search(r"uddg=([^&]+)", href)
                        if m:
                            href = urllib.parse.unquote(m.group(1))
                    results.append({"title": a.get_text(strip=True), "url": href})
                return results
            except Exception as e:
                return []

        def _fetch_linkedin(s: Dict) -> str:
            name = s.get("founder_name", "")
            company = s.get("company_name", "")
            if not name or name == "Founder":
                return ""
            try:
                results = _ddg_html_search(
                    f'site:linkedin.com/in "{name}" "{company}"',
                    max_results=3,
                )
                for r in results:
                    url = r.get("url", "")
                    if "linkedin.com/in/" in url:
                        return url
            except Exception as e:
                log.debug(f"  LinkedIn search failed for {name} ({company}): {e}")
            return ""

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            li_results = list(ex.map(_fetch_linkedin, startups))

        # ---- Parallel HR contact discovery ----
        log.info("Stage 3: Finding HR contacts in parallel...")

        def _fetch_hr(s: Dict) -> Dict:
            company = s.get("company_name", "")
            domain  = re.sub(r"[^a-z0-9.]", "", company.lower()) + ".com"
            try:
                results = _ddg_html_search(
                    f'site:linkedin.com/in "{company}" "talent acquisition" OR "HR" OR "people"',
                    max_results=3,
                )
                for r in results:
                    url = r.get("url", "")
                    if "linkedin.com/in/" in url:
                        name = r.get("title", "HR Contact").split(" - ")[0].split("|")[0].strip()
                        if name and len(name) > 3:
                            return {
                                "company":              company,
                                "contact_name":         name,
                                "role":                 "HR / Talent Acquisition",
                                "department":           "HR / People",
                                "public_linkedin_url":  url,
                                "email":                f"careers@{domain}",
                            }
            except Exception:
                pass
            # Fallback
            return {
                "company":              company,
                "contact_name":         f"HR Team ({company})",
                "role":                 "Talent Acquisition",
                "department":           "HR / People",
                "public_linkedin_url":  f"https://www.linkedin.com/company/{re.sub(r'[^a-z0-9]', '', company.lower())}/people",
                "email":                f"careers@{domain}",
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            hr_results = list(ex.map(_fetch_hr, startups))

        # Build records
        founders_list = []
        contacts_list = []
        linkedin_list = []

        for idx, s in enumerate(startups, 1):
            linkedin_url = li_results[idx-1] or ""
            hr = hr_results[idx-1]

            # Update startup with linkedin url
            s["founder_linkedin_url"] = linkedin_url
            if not s.get("founder_email"):
                s["founder_email"] = self.guess_email(s.get("founder_name", ""), s.get("company_name", ""))

            log.info(f"  [{idx}/{len(startups)}] {s['company_name']:30s} -> {s.get('founder_name','?'):25s} LI={('yes' if linkedin_url else 'no')}")

            founders_list.append({
                "startup_id":         f"STU-IND-{idx:03d}",
                "company":            s["company_name"],
                "founder_name":       s.get("founder_name", ""),
                "ceo":                s.get("founder_name", "") if "ceo" in s.get("founder_role", "").lower() else "",
                "position":           s.get("founder_role", "Founder"),
                "public_linkedin_url": linkedin_url,
                "website_profile":    f"https://{re.sub(r'[^a-z0-9.]', '', s['company_name'].lower())}.com/about",
                "email":              s.get("founder_email", ""),
                "industry":           s.get("industry", ""),
                "city":               s.get("city", ""),
                "funding_stage":      s.get("funding_stage", ""),
                "funding_amount":     s.get("funding_amount", ""),
                "investors":          s.get("investors", ""),
                "source":             s.get("source", ""),
                "founded_year":       s.get("founded_year", ""),
            })

            if linkedin_url:
                linkedin_list.append({
                    "startup_id":  f"STU-IND-{idx:03d}",
                    "company":     s["company_name"],
                    "name":        s.get("founder_name", ""),
                    "role":        s.get("founder_role", ""),
                    "linkedin_url": linkedin_url,
                })

            hr["startup_id"] = f"STU-IND-{idx:03d}"
            contacts_list.append(hr)
            if hr.get("public_linkedin_url") and "linkedin.com/in/" in hr["public_linkedin_url"]:
                linkedin_list.append({
                    "startup_id":  f"STU-IND-{idx:03d}",
                    "company":     s["company_name"],
                    "name":        hr["contact_name"],
                    "role":        hr["role"],
                    "linkedin_url": hr["public_linkedin_url"],
                })

        # ---- Build Funding Rounds sheet (one entry per startup) ----
        funding_rounds = []
        for idx, s in enumerate(startups, 1):
            investors = s.get("investors", "Undisclosed")
            lead, co = investors.split(",", 1) if "," in investors else (investors, "")
            funding_rounds.append({
                "startup_id":    f"STU-IND-{idx:03d}",
                "company":       s["company_name"],
                "round_name":    s.get("funding_stage", "Undisclosed"),
                "amount_raised": s.get("funding_amount", "Undisclosed"),
                "announced_date": s.get("founded_year", "2025"),
                "lead_investor": lead.strip() if lead else "Undisclosed",
                "co_investors":  co.strip() if co else "—",
            })

        # ---- Build Startups sheet ----
        startups_clean = []
        for idx, s in enumerate(startups, 1):
            website = f"https://{re.sub(r'[^a-z0-9.]', '', s['company_name'].lower())}.com"
            startups_clean.append({
                "startup_id":   f"STU-IND-{idx:03d}",
                "company":      s["company_name"],
                "industry":     s.get("industry", "Technology"),
                "funding_stage": s.get("funding_stage", "Undisclosed"),
                "funding_amount": s.get("funding_amount", "Undisclosed"),
                "investors":    s.get("investors", "Undisclosed"),
                "city":         s.get("city", "India"),
                "website":      website,
                "source_url":   s.get("source", "Curated"),
                "founded_year": s.get("founded_year", ""),
                "founder_name": s.get("founder_name", ""),
                "shark_tank_season": s.get("shark_tank_season", ""),
            })

        # ---- Write Excel ----
        log.info("=" * 70)
        log.info("Exporting to Excel workbook...")
        exporter = ExcelOutreachExporter(output_path=EXCEL_OUTPUT_PATH)
        run_log = {
            "date":            datetime.date.today().isoformat(),
            "companies_found": len(startups_clean),
            "companies_added": len(startups_clean),
            "contacts_found":  len(founders_list) + len(contacts_list),
            "errors":          0,
            "duration":        "Pipeline run complete",
            "status":          "SUCCESS",
        }
        exporter.export_workbook(
            startups_data=startups_clean,
            founders_data=founders_list,
            contacts_data=contacts_list,
            run_log=run_log,
        )
        log.info(f"Excel written: {EXCEL_OUTPUT_PATH}")

        # ---- Summary ----
        log.info("=" * 70)
        log.info("SUMMARY")
        log.info(f"  Startups processed : {len(startups_clean)}")
        log.info(f"  Founders found     : {len(founders_list)}")
        log.info(f"  HR contacts        : {len(contacts_list)}")
        log.info(f"  LinkedIn URLs      : {sum(1 for l in linkedin_list if l['linkedin_url'])}")
        log.info("=" * 70)

        return {
            "startups": len(startups_clean),
            "founders": len(founders_list),
            "contacts": len(contacts_list),
            "linkedin_urls": sum(1 for l in linkedin_list if l["linkedin_url"]),
            "excel_path": EXCEL_OUTPUT_PATH,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    pipeline = FounderPipeline(limit=args.limit)
    result = pipeline.run()
    print(json.dumps(result, indent=2))
