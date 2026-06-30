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

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "Referer": "https://www.google.com/",
        "DNT": "1",
    }

def is_blocked(html):
    if not html or len(html) < 1000:
        return True
    low = html.lower()
    return any(x in low for x in ["captcha", "access denied", "are you a human", "automated access"])

def is_dead_page(html):
    if not html:
        return False
    low = html.lower()
    return any(x in low for x in ["page not found", "404", "we are unable to find"]) and "pdpdata" not in low and "__myx" not in low

def fetch_myntra_html(style_id, max_attempts=3):
    url = f"https://www.myntra.com/{style_id}"
    for attempt in range(max_attempts):
        try:
            if attempt > 0:
                time.sleep(random.uniform(0.4, 1.0))
            session = requests.Session()
            session.headers.update(get_headers())
            resp = session.get(url, timeout=8, allow_redirects=True)
            if resp.status_code == 404:
                return None, None, "DEAD_PAGE"
            if resp.status_code in (403, 429, 503):
                continue
            if resp.status_code != 200:
                continue
            html = resp.text
            if is_blocked(html):
                continue
            return html, resp.url, None
        except Exception:
            continue
    return None, None, "Myntra blocked the request. Try again in a moment."

def extract_embedded_json(html):
    """Myntra embeds product data as window.__myx = {...} in a <script> tag."""
    patterns = [
        r"window\.__myx\s*=\s*(\{.*?\})\s*;?\s*</script>",
        r"pdpData\s*[:=]\s*(\{.*?\})\s*,\s*\"",
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            raw = m.group(1)
            # Try truncating progressively from the end in case of trailing garbage
            for end_trim in range(0, 200, 5):
                candidate = raw if end_trim == 0 else raw[:-end_trim]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
    return None

def parse_myntra(html, style_id, resolved_url=None):
    data = extract_embedded_json(html)

    title = None
    brand = None
    price = None
    mrp = None
    rating = None
    rating_count = "0"
    availability = "Unknown"

    if data:
        pdp = data.get("pdpData") or data.get("style") or data
        brand = (pdp.get("brand") or {}).get("name") if isinstance(pdp.get("brand"), dict) else pdp.get("brand")
        name = pdp.get("name") or pdp.get("productName")
        if brand and name:
            title = f"{brand} {name}"
        elif name:
            title = name

        pricing = pdp.get("price") or {}
        if isinstance(pricing, dict):
            price = pricing.get("discounted") or pricing.get("mrp")
            mrp = pricing.get("mrp")

        rr = pdp.get("ratings") or pdp.get("productRatingsAndReview") or {}
        if isinstance(rr, dict):
            avg = rr.get("averageRating") or rr.get("avgRating")
            cnt = rr.get("totalCount") or rr.get("totalRatingsCount")
            if avg:
                try:
                    rating = float(avg)
                except (TypeError, ValueError):
                    pass
            if cnt:
                rating_count = str(cnt)

        sizes = pdp.get("sizes") or []
        if sizes:
            in_stock = any(s.get("available", False) for s in sizes if isinstance(s, dict))
            availability = "In Stock" if in_stock else "Unavailable"
        elif price:
            availability = "In Stock"

    # Fallback: regex scan raw HTML if JSON parsing failed
    if not title:
        m = re.search(r'"name"\s*:\s*"([^"]{3,150})"', html)
        if m:
            title = m.group(1)
    if not price:
        m = re.search(r'"discounted"\s*:\s*([\d.]+)', html) or re.search(r'"mrp"\s*:\s*([\d.]+)', html)
        if m:
            try:
                price = float(m.group(1))
            except ValueError:
                pass
    if not rating:
        m = re.search(r'"averageRating"\s*:\s*"?([\d.]+)"?', html)
        if m:
            try:
                rating = float(m.group(1))
            except ValueError:
                pass
    if rating_count == "0":
        m = re.search(r'"totalCount"\s*:\s*(\d+)', html)
        if m:
            rating_count = m.group(1)

    if availability == "Unknown" and price:
        availability = "In Stock"
    elif availability == "Unknown":
        availability = "Unavailable"

    return {
        "asin": style_id,
        "style_id": style_id,
        "url": resolved_url or f"https://www.myntra.com/{style_id}",
        "title": title,
        "brand": brand,
        "price": price,
        "mrp": mrp,
        "currency": "INR",
        "rating": rating,
        "rating_count": rating_count,
        "availability": availability,
        "status": 200,
    }

def scrape(style_id):
    html, resolved_url, err = fetch_myntra_html(style_id)
    if err == "DEAD_PAGE":
        return {"error": "Product not found. Check the Style ID.", "status": 404, "dead": True}
    if err:
        return {"error": err, "status": 422}
    result = parse_myntra(html, style_id, resolved_url)
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
