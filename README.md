# Scout Dashboard

## 🌐 Live Dashboard
https://scrapingdashboard-snowy.vercel.app/

## 🖥️ Run Local Selenium Scraper (No API Key Needed)

The Vercel deployment uses basic HTTP requests which Amazon blocks.
To scrape without restrictions, run the local Selenium server:

### Step 1 — Install dependencies
```bash
pip install selenium webdriver-manager beautifulsoup4 lxml pandas openpyxl
```

### Step 2 — Start the local server
```bash
python local_server.py
```

### Step 3 — Open the dashboard
Visit https://scrapingdashboard-snowy.vercel.app/amazon

The dashboard auto-detects the local server and switches to it automatically.
You'll see **🖥️ Local Server** badge in the top right when connected.

### How it works
- Local server runs Chrome headlessly on your machine
- Mimics real browser behaviour — bypasses Amazon bot detection
- Dashboard on Vercel talks to your local server via `localhost:8000`
