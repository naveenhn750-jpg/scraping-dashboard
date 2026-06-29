from http.server import BaseHTTPRequestHandler
import json
import re
import os
import time
import random
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import requests

# ---------------------------------------------------------
# SCRAPER API CONFIG (key loaded from environment variable)
# ---------------------------------------------------------
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "")

def scraper_api_url(target_url):
    from urllib.parse import quote
    return f"https://app.scrapingbee.com/api/v1/?api_key={SCRAPER_API_KEY}&url={quote(target_url)}&render_js=false&premium_proxy=true"

# ---------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------

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
    if match:
        return match.group(1)
    return "0"

def extract_data(soup):
    data = {"Price": "0", "Rating": "0", "Rating_Count": "0"}

    # --- PRICE ---
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

    if not raw_price or raw_price == "0":
        whole_price = soup.select_one(".a-price-whole")
        if whole_price:
            raw_price = clean_only_numbers(whole_price.get_text(strip=True))

    if raw_price:
        data["Price"] = raw_price.split(".")[0] if "." in raw_price else raw_price

    availability = soup.select_one("#availability")
    if availability and "currently unavailable" in availability.get_text(strip=True).lower():
        data["Price"] = "0"

    # --- RATING COUNT ---
    review_tag = soup.find("span", id="acrCustomerReviewText")
    if review_tag:
        data["Rating_Count"] = parse_count_to_number(review_tag.get_text(strip=True))

    # --- RATING ---
    rating_tag = soup.select_one("span[data-hook='rating-out-of-text']")
    if not rating_tag:
        rating_tag = soup.select_one(".a-icon-star .a-icon-alt")
    if rating_tag:
        data["Rating"] = clean_only_numbers(rating_tag.get_text(strip=True))

    if data["Rating_Count"] == "0":
        data["Rating"] = "0"

    return data

def is_blocked(html):
    if not html or len(html) < 500:
        return True
    low = html.lower()
    return any(x in low for x in [
        "captcha", "robot check", "validatecaptcha",
        "enter the characters", "not a robot",
        "api-services-support@amazon.com",
        "automated access",
    ])

def is_dead_page(html):
    if not html:
        return False
    low = html.lower()
    return any(x in low for x in [
        "looking for something",
        "not a functioning page",
        "dogs of amazon",
        "we couldn't find that page",
    ])

def fetch_page(url):
    try:
        proxy_url = scraper_api_url(url)
        resp = requests.get(proxy_url, timeout=60)
        if resp.status_code == 200:
            html = resp.text
            if is_dead_page(html):
                return None, "DEAD_PAGE"
            if is_blocked(html):
                return None, "Amazon blocked the request. Try again."
            return html, None
        elif resp.status_code == 401:
            return None, "Invalid API key."
        elif resp.status_code == 403:
            return None, "API quota exceeded."
        else:
            return None, f"Request failed with status {resp.status_code}."
    except Exception as e:
        return None, f"Request error: {str(e)}"

def scrape(asin, domain):
    try:
        url = f"https://www.{domain}/dp/{asin}?th=1&psc=1"
        html, err = fetch_page(url)

        if err == "DEAD_PAGE":
            return {"error": "Product does not exist. The ASIN may be invalid or delisted.", "status": 404, "dead": True}
        if err:
            return {"error": err, "status": 422}

        soup = BeautifulSoup(html, "html.parser")
        result = extract_data(soup)

        title_tag = soup.find("span", {"id": "productTitle"})
        title = title_tag.get_text(strip=True) if title_tag else None

        avail_tag = soup.find("div", {"id": "availability"})
        availability = "Unknown"
        if avail_tag:
            txt = avail_tag.get_text(strip=True).lower()
            if "in stock" in txt:
                availability = "In Stock"
            elif "only" in txt and "left" in txt:
                availability = "Low Stock"
            elif any(x in txt for x in ["out of stock", "unavailable", "not available"]):
                availability = "Out of Stock"
            else:
                availability = avail_tag.get_text(strip=True)[:40]
        elif result["Price"] != "0":
            availability = "In Stock"
        else:
            availability = "Out of Stock"

        if not title and result["Price"] == "0":
            return {"error": "Could not extract data. Try again.", "status": 422}

        return {
            "asin": asin,
            "url": f"https://www.{domain}/dp/{asin}",
            "title": title,
            "price": float(result["Price"]) if result["Price"] and result["Price"] != "0" else None,
            "currency": "INR" if domain == "amazon.in" else "USD",
            "rating": float(result["Rating"]) if result["Rating"] and result["Rating"] != "0" else None,
            "rating_count": result["Rating_Count"],
            "availability": availability,
            "status": 200,
        }
    except Exception as e:
        return {"error": str(e), "status": 500}

def to_json(data):
    try:
        return json.dumps(data, ensure_ascii=False).encode("utf-8")
    except Exception:
        return b'{"error":"JSON encode failed","status":500}'

class handler(BaseHTTPRequestHandler):
    def send_json(self, data):
        body = to_json(data)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            asin   = params.get("asin",   [""])[0].strip().upper()
            domain = params.get("domain", ["amazon.in"])[0].strip()
            allowed = ["amazon.in", "amazon.com", "amazon.co.uk", "amazon.de", "amazon.co.jp"]

            if not asin or not re.match(r"^[A-Z0-9]{10}$", asin):
                self.send_json({"error": "Invalid ASIN.", "status": 400})
                return
            if domain not in allowed:
                self.send_json({"error": "Unsupported domain.", "status": 400})
                return

            self.send_json(scrape(asin, domain))
        except Exception as e:
            self.send_json({"error": str(e), "status": 500})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass
