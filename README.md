# 🚀 AI Indian Startup Funding & Founder Intelligence

A production-ready, backend-focused automation platform built with **Python 3**, **FastAPI**, **Gemini AI**, and **n8n** to discover **recently funded Indian startups (2025+)** and extract **founder/leadership intelligence** — LinkedIn profiles, emails, roles, and funding details.

The system automatically discovers funded startups across top VCs and tech media, extracts **funding amount, round, investors, and date**, finds **founders, C-suite, HR, and tech leads**, scrapes **LinkedIn profiles and contact details**, and exports a **6-Sheet Excel Reporting Workbook (`excel/Indian_Startup_Outreach.xlsx`)**.

The pipeline runs **daily at 1:00 AM IST** by default, scraping the web and LinkedIn. On each run it refreshes existing startups and discovers new ones, maintaining a target of **50-100 startups**.

---

## 🏗️ System Architecture & 6-Stage Pipeline

```text
1. Startup Discovery (VC Portals & Tech Media, 2025+ funding)
        │
        ▼
2. AI Clean, Score & Enrich Funding Data (Gemini)
        │
        ▼
3. Founder & C-Suite Discovery (LinkedIn, team pages, web search)
        │
        ▼
4. Key Contact Discovery (HR, Tech Leads, Management)
        │
        ▼
5. LinkedIn Profile & Email Extraction
        │
        ▼
6. 6-Sheet Excel Export (`excel/Indian_Startup_Outreach.xlsx`)
```

---

## 📂 Project Directory Layout

```text
FTB/
├── src/
│   ├── ai/                  # Gemini AI processing
│   ├── config.py            # Settings, paths, queries
│   ├── exporters/           # 6-Sheet Excel Workbook Builder
│   ├── pipeline/            # Orchestrator + Scheduler
│   ├── scrapers/            # Web, LinkedIn, funding scrapers
│   └── utils/               # Scoring engine
├── app/
│   └── main.py              # FastAPI Application Server
├── n8n/
│   └── workflows/
│       └── startup_outreach_workflow.json
├── excel/                   # Output Excel Workbooks
├── logs/                    # System & execution logs
├── data/                    # Data stores
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── index.html               # Control Hub & Live Preview UI
├── requirements.txt
└── README.md
```

---

## 📊 6-Sheet Excel Workbook Specification

The system generates **`excel/Indian_Startup_Outreach.xlsx`** featuring 6 dedicated sheets auto-sorted by Priority Score:

1. **`Startups`**: Startup ID, Company, Industry, Funding Stage, Funding Amount, Investors, City, Website, Source URL
2. **`Funding Rounds`**: Startup ID, Company, Round Name, Amount Raised, Announced Date, Lead Investor, Co-Investors
3. **`Founders & Leadership`**: Startup ID, Company, Founder Name, CEO, Position, LinkedIn URL, Website Profile, Email
4. **`Key Contacts`**: Startup ID, Company, Contact Name, Role, Department, LinkedIn URL, Email
5. **`LinkedIn Profiles`**: Startup ID, Company, Name, Role, LinkedIn URL
6. **`Logs`**: Date, Companies Found, Companies Added, Contacts Found, Errors, Duration, Status

---

## ⚡ Priority Scoring Algorithm

Each startup is assigned a dynamic score (0 - 100):
- **Funding Stage Weight**: Unicorn / Series D+ (+40 pts), Series A/B (+30 pts), Seed (+20 pts)
- **Funding Amount**: $50M+ (+30 pts), $10M+ (+20 pts), $1M+ (+10 pts)
- **Indian HQ Signal**: Verified HQ in India (+15 pts)
- **Hot Sector**: AI, FinTech, HealthTech, DeepTech (+10 pts)

---

## 🌐 FastAPI REST API Endpoints

Run server with `python app/main.py` (Default: `http://localhost:8000`):

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Health check & service status |
| `GET` | `/startups` | Returns discovered Indian startups (2025+ funding, priority sorted) |
| `GET` | `/founders?company=Zepto` | Returns founders, CEO, CTO with LinkedIn & email |
| `GET` | `/contacts?company=Zepto` | Returns HR, tech leads, management with LinkedIn & email |
| `GET` | `/export` | Downloads generated 6-sheet Excel workbook |
| `POST` | `/run?limit=100` | Triggers funding & founder intelligence pipeline |
| `GET` | `/schedule/status` | Daily 1AM IST scheduler status |

---

## 🐳 Deployment & Execution

### 1. Start FastAPI Server
```bash
python app/main.py
```

### 2. Trigger Pipeline for 100 Startups
```bash
curl -X POST "http://localhost:8000/run?limit=100"
```

### 3. Deploy via Docker Compose
```bash
cd docker
docker-compose up -d
```

---

## 🔮 Future Enhancements
- LinkedIn profile scraping via official API / Playwright
- Automated daily monitoring of portfolio companies
- CRM synchronization (HubSpot, Airtable, Notion)
- Email verification via SMTP checks
- Investor relationship mapping
