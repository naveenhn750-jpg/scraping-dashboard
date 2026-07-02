from http.server import BaseHTTPRequestHandler
import json
import re
import time
import random
import os
from urllib.parse import urlparse, parse_qs
import requests

SCRAPINGBEE_KEY = os.environ.get("SCRAPER_API_KEY", "")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://www.google.com/",
        "DNT": "1",
    }

def has_product_data(html):
    if not html:
        return False
    low = html.lower()
    return any(x in low for x in ["__myx", "pdpdata", "discounted", "og:title", "pdp-title"])

def is_blocked(html):
    if not html or len(html) < 500:
        return True
    low = html.lower()
    return any(x in low for x in ["captcha", "access denied", "are you a human", "automated access"])

def is_dead_page(html):
    if not html:
        return False
    low = html.lower()
    return "page not found" in low and "pdpdata" not in low

def fetch_via_scrapingbee(url):
    """Fetch using ScrapingBee with JS rendering - needed for Myntra SPA."""
    if not SCRAPINGBEE_KEY:
        return None, None, "No proxy API key set."
    try:
        resp = requests.get(
            "https://app.scrapingbee.com/api/v1/",
            params={
                "api_key": SCRAPINGBEE_KEY,
                "url": url,
                "render_js": "true",
                "premium_proxy": "true",
                "country_code": "in",
                "wait": "4000",
                "block_ads": "true",
                "stealth_proxy": "true",
            },
            timeout=60,
        )
        if resp.status_code == 200:
            html = resp.text
            final_url = resp.headers.get("Spb-Resolved-Url") or url
            if is_dead_page(html):
                return None, None, "DEAD_PAGE"
            return html, final_url, None
        elif resp.status_code == 401:
            return None, None, "Invalid proxy API key."
        elif resp.status_code == 403:
            return None, None, "Proxy quota exceeded."
        elif resp.status_code == 422:
            return None, None, f"Proxy error 422: {resp.text[:200]}"
        else:
            return None, None, f"Proxy HTTP {resp.status_code}: {resp.text[:200]}"
    except requests.exceptions.Timeout:
        return None, None, "Proxy timed out."
    except Exception as e:
        return None, None, f"Proxy exception: {str(e)}"

def fetch_direct(url):
    """Try direct fetch first - faster but likely blocked by Myntra."""
    try:
        session = requests.Session()
        session.headers.update(get_headers())
        try:
            session.get("https://www.myntra.com/", timeout=5)
        except Exception:
            pass
        session.headers.update({"Referer": "https://www.myntra.com/", "Sec-Fetch-Site": "same-origin"})
        resp = session.get(url, timeout=8, allow_redirects=True)
        if resp.status_code == 200 and has_product_data(resp.text):
            return resp.text, resp.url, None
        return None, None, f"Direct fetch failed: {resp.status_code}"
    except Exception as e:
        return None, None, f"Direct error: {str(e)}"

def fetch_myntra(style_id):
    url = f"https://www.myntra.com/{style_id}"

    # Try direct first (free, fast)
    html, resolved_url, err = fetch_direct(url)
    if html:
        return html, resolved_url, None

    # Fall back to ScrapingBee (JS rendering, bypasses bot protection)
    html, resolved_url, err = fetch_via_scrapingbee(url)
    if html:
        return html, resolved_url, None

    return None, None, err or "Could not fetch product. Try again."

def extract_embedded_json(html):
    patterns = [
        r"window\.__myx\s*=\s*(\{.*?\})\s*;?\s*</script>",
        r"pdpData\s*[:=]\s*(\{.*?\})\s*[,;]",
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            raw = m.group(1)
            for trim in range(0, 500, 10):
                candidate = raw if trim == 0 else raw[:-trim]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
    return None

def parse_myntra(html, style_id, resolved_url=None):
    data = extract_embedded_json(html)
    title = brand = price = mrp = rating = None
    rating_count = "0"
    availability = "Unknown"

    if data:
        pdp = data.get("pdpData") or data.get("style") or data
        brand_data = pdp.get("brand") or {}
        brand = brand_data.get("name") if isinstance(brand_data, dict) else str(brand_data)
        name  = pdp.get("name") or pdp.get("productName")
        title = f"{brand} {name}" if brand and name else name

        pricing = pdp.get("price") or {}
        if isinstance(pricing, dict):
            price = pricing.get("discounted") or pricing.get("mrp")
            mrp   = pricing.get("mrp")

        rr = pdp.get("ratings") or pdp.get("productRatingsAndReview") or {}
        if isinstance(rr, dict):
            avg = rr.get("averageRating") or rr.get("avgRating")
            cnt = rr.get("totalCount") or rr.get("totalRatingsCount")
            if avg:
                try: rating = float(avg)
                except: pass
            if cnt:
                rating_count = str(cnt)

        sizes = pdp.get("sizes") or []
        if sizes:
            in_stock = any(s.get("available", False) for s in sizes if isinstance(s, dict))
            availability = "In Stock" if in_stock else "Unavailable"
        elif price:
            availability = "In Stock"

    # HTML regex fallbacks
    if not title:
        for pat in [r'"og:title"\s+content="([^"]{3,150})"', r'"name"\s*:\s*"([^"]{3,150})"']:
            m = re.search(pat, html)
            if m:
                title = m.group(1)
                break
    if not price:
        for pat in [r'"discounted"\s*:\s*([\d.]+)', r'"mrp"\s*:\s*([\d.]+)'
                    r'class="pdp-price"[^>]*>.*?₹\s*([\d,]+)']:
            m = re.search(pat, html)
            if m:
                try:
                    price = float(m.group(1).replace(",", ""))
                    break
                except: pass
    if not rating:
        m = re.search(r'"averageRating"\s*:\s*"?([\d.]+)"?', html)
        if m:
            try: rating = float(m.group(1))
            except: pass
    if rating_count == "0":
        m = re.search(r'"totalCount"\s*:\s*(\d+)', html)
        if m:
            rating_count = m.group(1)

    if availability == "Unknown":
        availability = "In Stock" if price else "Unavailable"

    return {
        "asin": style_id,
        "style_id": style_id,
        "url": resolved_url or f"https://www.myntra.com/{style_id}",
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
    html, resolved_url, err = fetch_myntra(style_id)
    if err == "DEAD_PAGE":
        return {"error": "Product not found. Check the Style ID.", "status": 404, "dead": True}
    if err and not html:
        return {"error": err, "status": 422}
    result = parse_myntra(html, style_id, resolved_url)
    if not result["title"] and not result["price"]:
        return {"error": "Could not extract product data. Try again.", "status": 422}
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
            params   = parse_qs(urlparse(self.path).query)
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
