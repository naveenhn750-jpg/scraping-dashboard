from http.server import BaseHTTPRequestHandler
import json
import re
import time
import random
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import requests

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

def is_blocked(html):
    if not html or len(html) < 500:
        return True
    low = html.lower()
    return any(x in low for x in ["captcha", "robot check", "validatecaptcha", "automated access"])

def is_dead_page(html):
    if not html:
        return False
    low = html.lower()
    return any(x in low for x in ["looking for something", "dogs of amazon", "we couldn't find that page"])

def fetch_with_retry(url, max_attempts=4):
    session = requests.Session()
    for attempt in range(max_attempts):
        try:
            if attempt > 0:
                time.sleep(random.uniform(0.3, 0.8))
            session.headers.update(get_headers())
            resp = session.get(url, timeout=8, allow_redirects=True)
            if resp.status_code == 404:
                return None, "DEAD_PAGE"
            if resp.status_code in (503, 429, 403):
                continue
            if resp.status_code != 200:
                continue
            html = resp.text
            if is_dead_page(html):
                return None, "DEAD_PAGE"
            if is_blocked(html):
                continue
            return html, None
        except Exception:
            continue
    return None, "Amazon blocked the request. Try again in a moment."

def parse_amazon(html, asin, domain):
    soup = BeautifulSoup(html, "html.parser")

    title = None
    t = soup.find("span", {"id": "productTitle"})
    if t:
        title = t.get_text(strip=True)

    price = None
    price_selectors = [
        "#corePriceDisplay_desktop_feature_div .a-offscreen",
        "#corePrice_feature_div .a-offscreen",
        "#apex_desktop .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        ".a-price .a-offscreen",
    ]
    for sel in price_selectors:
        el = soup.select_one(sel)
        if el:
            raw = re.sub(r"[^\d.]", "", el.get_text(strip=True).replace(",", ""))
            if raw:
                try:
                    price = float(raw.split(".")[0])
                    if 1 <= price <= 500000:
                        break
                    price = None
                except:
                    pass

    if not price:
        w = soup.select_one(".a-price-whole")
        if w:
            raw = re.sub(r"[^\d]", "", w.get_text(strip=True))
            try:
                price = float(raw) if raw else None
            except:
                pass

    rating = None
    r = soup.select_one("span[data-hook='rating-out-of-text']") or soup.find("span", {"class": "a-icon-alt"})
    if r:
        m = re.search(r"([\d.]+)\s*out\s*of\s*5", r.get_text())
        if m:
            rating = float(m.group(1))

    rating_count = "0"
    rc = soup.find("span", id="acrCustomerReviewText")
    if rc:
        txt = rc.get_text(strip=True).upper().replace(",", "").replace("(","").replace(")","")
        if "K" in txt:
            m = re.search(r"([\d.]+)", txt)
            rating_count = str(int(float(m.group(1)) * 1000)) if m else "0"
        elif "M" in txt:
            m = re.search(r"([\d.]+)", txt)
            rating_count = str(int(float(m.group(1)) * 1000000)) if m else "0"
        else:
            m = re.search(r"(\d+)", txt)
            rating_count = m.group(1) if m else "0"

    availability = "Unknown"
    av = soup.find("div", {"id": "availability"})
    if av:
        txt = av.get_text(strip=True).lower()
        if "in stock" in txt:
            availability = "In Stock"
        elif "only" in txt and "left" in txt:
            availability = "Low Stock"
        elif any(x in txt for x in ["out of stock", "unavailable", "not available", "currently unavailable"]):
            availability = "Out of Stock"
        else:
            availability = av.get_text(strip=True)[:40]
    elif price:
        availability = "In Stock"
    else:
        availability = "Out of Stock"

    return {
        "asin": asin,
        "url": f"https://www.{domain}/dp/{asin}",
        "title": title,
        "price": price,
        "currency": "INR" if domain == "amazon.in" else "USD",
        "rating": rating,
        "rating_count": rating_count,
        "availability": availability,
        "status": 200,
    }

def scrape(asin, domain):
    try:
        url = f"https://www.{domain}/dp/{asin}?th=1&psc=1"
        html, err = fetch_with_retry(url, max_attempts=4)
        if err == "DEAD_PAGE":
            return {"error": "Product not found or delisted.", "status": 404, "dead": True}
        if err:
            return {"error": err, "status": 422}
        data = parse_amazon(html, asin, domain)
        if not data["title"] and not data["price"]:
            return {"error": "Could not extract data. Try again.", "status": 422}
        return data
    except Exception as e:
        return {"error": str(e), "status": 500}

class handler(BaseHTTPRequestHandler):
    def send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
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
