# Italy Web Agency & System Integrator Intelligence Tool 🇮🇹

A premium **Streamlit web application** that automatically crawls, validates, and extracts intelligence about Italian web agencies and system integrators. It accepts single searches (by agency name or VAT) or batch-processes list uploads via CSV.

## Features

- **Official Registry Lookup**: Integrates with the EU Commission VIES SOAP API to validate Italian VAT numbers (`Partita IVA`) and extract the official corporate name and registered address.
- **Auto-Discovery by Name**: Searches public company registers (e.g. Ufficio Camerale, Report Aziende) via DuckDuckGo to match names with VAT numbers.
- **Deep Site Crawling**: Traverses the agency homepage and priority pages (`/servizi`, `/contatti`, `/portfolio`, `/chi-siamo`, `/legal`) using a polite scraping queue to gather contact details.
- **Client Portfolio Extractor**: Scrapes client domains from case studies and portfolio directories.
- **Payment Gateway Analysis**: Confirms eCommerce integration support and highlights supported checkout providers (e.g., Stripe, PayPal, Nexi, Klarna).
- **LinkedIn Point of Contact Finder**: Utilizes target indexing to identify founders, CEOs, and developers on LinkedIn.
- **Consolidated Batch Uploads**: Supports uploading a CSV of target agencies, processing them asynchronously, and exporting unified Excel/CSV and JSON summaries.

---

## Installation & Setup

1. **Clone or Navigate to the Directory**:
   ```bash
   cd /Users/alberto/Documents/antigravity/happy-pythagoras
   ```

2. **Install Dependencies**:
   It is recommended to run this inside a virtual environment (e.g. `venv`):
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Install Package Locally**:
   ```bash
   pip install -e .
   ```

---

## Configuration & API Keys

By default, the tool **requires zero API keys** and runs completely free using **DuckDuckGo Search** for indexing. 

If you are performing high-volume batch jobs and experience DDG rate limits, you can provide API keys via a `.env` file in the root directory or configure them directly in the Streamlit Sidebar.

### `.env` File Template:
Create a `.env` file in the root:
```env
# Optional search engines: duckduckgo (default), serpapi, google
SEARCH_ENGINE=duckduckgo

# If using SerpAPI
SERPAPI_KEY=your_serp_api_key_here

# If using Google Custom Search JSON API
GOOGLE_API_KEY=your_google_key
GOOGLE_CX=your_cx_engine_id

# Crawling settings
MAX_CRAWL_DEPTH=2
MAX_CRAWL_PAGES=12
REQUEST_TIMEOUT=15
```

---

## Running the Web App

Start the Streamlit application using:
```bash
streamlit run app.py
```

This will spin up a local server, usually opening at `http://localhost:8501`.

---

## Verification & Testing

To run the unit test suite and verify regex/SOAP extraction engines:
```bash
python -m unittest discover -s tests
```
