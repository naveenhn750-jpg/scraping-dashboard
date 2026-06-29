from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import re
import time
import random
import threading
from urllib.parse import urlparse, parse_qs

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# ── Shared driver (reuse across requests) ──────────────────
_driver = None
_driver_lock = threading.Lock()

def get_driver():
    global _driver
    if _driver:
        return _driver
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--log-level=3")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ]
    options.add_argument(f"user-agent={random.choice(user_agents)}")
    _driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    print("✅ Chrome driver started")
    return _driver

def clean_only_numbers(text):
    if not text:
        return ""
    text = text.replace(",", "")
    match = re.search(r"(\d+(\.\d+)?)", text)
    return match.group(1) if match else ""

def parse_count_to_number(text):
    if not text:
        return "0"
    clean_text = text.upper().replace(",", "").replace("(", "").replace(")", "").strip()
    if 'K' in clean_text:
        match = re.search(r"(\d+(\.\d+)?)", clean_text)
        if match:
            return str(int(float(match.group(1)) * 1000))
    elif 'M' in clean_text:
        match = re.search(r"(\d+(\.\d+)?)", clean_text)
        if match:
            return str(int(float(match.group(1)) * 1000000))
    match = re.search(r"(\d+)", clean_text)
    return match.group(1) if match else "0"

def extract_data(soup, asin, domain):
    data = {
        "asin": asin,
        "url": f"https://www.{domain}/dp/{asin}",
        "title": "",
        "price": None,
        "currency": "INR" if domain == "amazon.in" else "USD",
        "rating": None,
        "rating_count": None,
        "availability": "Unknown"
    }

    # Title
    title_tag = soup.find("span", {"id": "productTitle"})
    if title_tag:
        data["title"] = title_tag.get_text(strip=True)

    # Price
    raw_price = ""
    price_selectors = [
        "#corePriceDisplay_desktop_feature_div .a-offscreen",
        "#corePrice_feature_div .a-offscreen",
        "#apex_desktop .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        ".a-price.a-text-price.a-size-medium.apexPriceToPay .a-offscreen",
        ".a-price .a-offscreen"
    ]
    for selector in price_selectors:
        element = soup.select_one(selector)
        if element and element.get_text(strip=True):
            raw_price = clean_only_numbers(element.get_text(strip=True))
            if raw_price:
                break
    if not raw_price:
        whole = soup.select_one(".a-price-whole")
        if whole:
            raw_price = clean_only_numbers(whole.get_text(strip=True))
    if raw_price:
        price_str = raw_price.split(".")[0] if "." in raw_price else raw_price
        try:
            data["price"] = float(price_str)
        except:
            pass

    # Availability
    avail_tag = soup.select_one("#availability")
    if avail_tag:
        txt = avail_tag.get_text(strip=True).lower()
        if "in stock" in txt:
            data["availability"] = "In Stock"
        elif "only" in txt and "left" in txt:
            data["availability"] = "Low Stock"
        elif "currently unavailable" in txt or "out of stock" in txt:
            data["availability"] = "Out of Stock"
            data["price"] = None
        else:
            data["availability"] = avail_tag.get_text(strip=True)[:40]
    elif data["price"]:
        data["availability"] = "In Stock"

    # Rating
    rating_tag = soup.select_one("span[data-hook='rating-out-of-text']")
    if not rating_tag:
        rating_tag = soup.select_one(".a-icon-star .a-icon-alt")
    if rating_tag:
        r = clean_only_numbers(rating_tag.get_text(strip=True))
        try:
            data["rating"] = float(r)
        except:
            pass

    # Rating count
    review_tag = soup.find("span", id="acrCustomerReviewText")
    if review_tag:
        data["rating_count"] = int(parse_count_to_number(review_tag.get_text(strip=True)))

    if not data["rating_count"]:
        data["rating"] = None

    return data

def scrape(asin, domain):
    with _driver_lock:
        try:
            driver = get_driver()
            url = f"https://www.{domain}/dp/{asin}?th=1&psc=1"
            print(f"  → Fetching {url}")
            driver.get(url)
            time.sleep(random.uniform(3.0, 5.0))

            soup = BeautifulSoup(driver.page_source, "html.parser")
            page_text = soup.get_text().lower()
            title_text = driver.title.lower()

            if "captcha" in page_text or "robot check" in title_text:
                return {"error": "Amazon showed a CAPTCHA. Wait a moment and retry.", "status": 503}

            if any(x in page_text for x in ["looking for something", "dogs of amazon", "not a functioning page"]):
                return {"error": "Product not found. The ASIN may be invalid or delisted.", "status": 404, "dead": True}

            data = extract_data(soup, asin, domain)

            if not data["title"] and not data["price"]:
                return {"error": "Could not extract data. Amazon may have changed layout. Retry.", "status": 422}

            data["status"] = 200
            return data

        except Exception as e:
            return {"error": f"Scraper error: {str(e)}", "status": 500}

class handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/product":
            params = parse_qs(parsed.query)
            asin   = params.get("asin",   [""])[0].strip().upper()
            domain = params.get("domain", ["amazon.in"])[0].strip()
            allowed = ["amazon.in", "amazon.com", "amazon.co.uk", "amazon.de"]
            if not asin or not re.match(r"^[A-Z0-9]{10}$", asin):
                self.send_json({"error": "Invalid ASIN.", "status": 400}, 400)
                return
            if domain not in allowed:
                self.send_json({"error": "Unsupported domain.", "status": 400}, 400)
                return
            result = scrape(asin, domain)
            self.send_json(result)
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        print(f"  [{args[1]}] {args[0] % args[2:]}")

if __name__ == "__main__":
    print("🚀 Starting local Amazon scraper server on http://localhost:8000")
    print("   Dashboard should point to http://localhost:8000/api/product")
    print("   Press Ctrl+C to stop\n")
    server = HTTPServer(("0.0.0.0", 8000), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Server stopped.")
        if _driver:
            _driver.quit()
