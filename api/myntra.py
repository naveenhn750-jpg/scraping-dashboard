from http.server import BaseHTTPRequestHandler
import json
import re
import time
import random
from urllib.parse import urlparse, parse_qs
import requests

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

def get_headers(style_id):
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": f"https://www.myntra.com/{style_id}",
        "x-myntraweb": "Yes",
        "x-meta-app": "channel=web",
        "DNT": "1",
    }

def fetch_myntra_pdp(style_id, max_attempts=4):
    """Myntra exposes a public PDP JSON API used by their own web frontend."""
    url = f"https://www.myntra.com/gateway/v2/product/{style_id}"
    for attempt in range(max_attempts):
        try:
            if attempt > 0:
                time.sleep(random.uniform(0.5, 1.2))
            resp = requests.get(url, headers=get_headers(style_id), timeout=8)
            if resp.status_code == 404:
                return None, "DEAD_PAGE"
            if resp.status_code in (403, 429, 503):
                continue
            if resp.status_code != 200:
                continue
            data = resp.json()
            if not data or "style" not in data:
                continue
            return data, None
        except (requests.exceptions.RequestException, ValueError):
            continue
    return None, "Myntra blocked the request. Try again in a moment."

def parse_myntra(data, style_id):
    style = data.get("style", {})

    title = None
    brand = style.get("brand", {}).get("name") if isinstance(style.get("brand"), dict) else None
    name = style.get("name") or style.get("productName")
    if brand and name:
        title = f"{brand} {name}"
    elif name:
        title = name

    price = None
    mrp = None
    pricing = style.get("price") or {}
    if isinstance(pricing, dict):
        price = pricing.get("discounted") or pricing.get("mrp")
        mrp = pricing.get("mrp")

    rating = None
    rating_count = "0"
    rr = style.get("ratings") or {}
    if isinstance(rr, dict):
        avg = rr.get("averageRating")
        cnt = rr.get("totalCount")
        if avg:
            try:
                rating = float(avg)
            except (TypeError, ValueError):
                rating = None
        if cnt:
            rating_count = str(cnt)

    in_stock = style.get("sizes") and any(
        s.get("available", False) for s in style.get("sizes", []) if isinstance(s, dict)
    )
    if not style.get("sizes"):
        availability = "Unknown"
    elif in_stock:
        availability = "In Stock"
    else:
        availability = "Unavailable"

    return {
        "style_id": style_id,
        "url": f"https://www.myntra.com/{style_id}",
        "title": title,
        "brand": brand,
        "price": float(price) if price else None,
        "mrp": float(mrp) if mrp else None,
        "currency": "INR",
        "rating": rating,
        "rating_count": rating_count,
        "availability": availability,
        "status": 200,
    }

def scrape(style_id):
    data, err = fetch_myntra_pdp(style_id)
    if err == "DEAD_PAGE":
        return {"error": "Product not found. Check the Style ID.", "status": 404, "dead": True}
    if err:
        return {"error": err, "status": 422}
    result = parse_myntra(data, style_id)
    if not result["title"] and not result["price"]:
        return {"error": "Could not extract data. Try again.", "status": 422}
    return result

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
            style_id = params.get("style_id", [""])[0].strip()
            if not style_id or not re.match(r"^\d{4,12}$", style_id):
                self.send_json({"error": "Invalid Style ID.", "status": 400})
                return
            self.send_json(scrape(style_id))
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
