from http.server import BaseHTTPRequestHandler
import json
import re
import os
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import requests

SCRAPINGBEE_KEY = os.environ.get("SCRAPER_API_KEY", "")

def fetch_amazon(url):
    """Fetch Amazon page via ScrapingBee proxy."""
    if not SCRAPINGBEE_KEY:
        return None, "API key not configured."
    try:
        resp = requests.get(
            "https://app.scrapingbee.com/api/v1/",
            params={
                "api_key": SCRAPINGBEE_KEY,
                "url": url,
                "render_js": "false",
                "premium_proxy": "true",
                "country_code": "in",
            },
            timeout=8
        )
        if resp.status_code == 200:
            html = resp.text
            if len(html) < 500:
                return None, "Empty response from Amazon."
            low = html.lower()
            if any(x in low for x in ["captcha", "robot check", "validatecaptcha", "automated access"]):
                return None, "Amazon blocked the request."
            if any(x in low for x in ["looking for something", "dogs of amazon", "we couldn't find that page"]):
                return None, "DEAD_PAGE"
            return html, None
        elif resp.status_code == 401:
            return None, "Invalid ScrapingBee API key."
        elif resp.status_code == 403:
            return None, "ScrapingBee quota exceeded."
        elif resp.status_code == 429:
            return None, "Too many requests. Try again later."
        else:
            return None, f"ScrapingBee error: {resp.status_code}"
    except requests.exceptions.Timeout:
        return None, "Request timed out."
    except Exception as e:
        return None, f"Error: {str(e)}"

def parse_amazon(html, asin, domain):
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = None
    t = soup.find("span", {"id": "productTitle"})
    if t:
        title = t.get_text(strip=True)

    # Price
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

    # Rating
    rating = None
    r = soup.select_one("span[data-hook='rating-out-of-text']") or soup.find("span", {"class": "a-icon-alt"})
    if r:
        m = re.search(r"([\d.]+)\s*out\s*of\s*5", r.get_text())
        if m:
            rating = float(m.group(1))

    # Rating count
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

    # Availability
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
    url = f"https://www.{domain}/dp/{asin}?th=1&psc=1"
    html, err = fetch_amazon(url)
    if err == "DEAD_PAGE":
        return {"error": "Product not found or delisted.", "status": 404, "dead": True}
    if err:
        return {"error": err, "status": 422}
    data = parse_amazon(html, asin, domain)
    if not data["title"] and not data["price"]:
        return {"error": "Could not extract data. Try again.", "status": 422}
    return data

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
            key_set = bool(SCRAPINGBEE_KEY)
            result = scrape(asin, domain)
            result["_key_loaded"] = key_set
            self.send_json(result)
        except Exception as e:
            import traceback
            self.send_json({"error": str(e), "trace": traceback.format_exc()[-500:], "status": 500})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass
