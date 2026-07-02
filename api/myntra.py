from http.server import BaseHTTPRequestHandler
import json
import random
import re
import time
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, unquote
import requests


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]


DEFAULT_PRODUCT_PATH = "trousers/spykar/spykar-men-straight-fit-mid-rise-cargos"


def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Referer": "https://www.google.com/",
        "DNT": "1",
    }


def is_blocked(html):
    if not html or len(html) < 500:
        return True
    low = html.lower()
    return any(
        token in low
        for token in [
            "access denied",
            "captcha",
            "forbidden",
            "robot",
            "request blocked",
        ]
    )


def is_dead_page(html):
    if not html:
        return False
    low = html.lower()
    return any(
        token in low
        for token in [
            "404",
            "page you are looking for",
            "we couldn't find",
            "product you are looking for is unavailable",
        ]
    )


def fetch_with_retry(url, max_attempts=3):
    for attempt in range(max_attempts):
        try:
            if attempt > 0:
                time.sleep(random.uniform(0.4, 1.0))
            session = requests.Session()
            session.headers.update(get_headers())
            resp = session.get(url, timeout=12, allow_redirects=True)
            if resp.status_code == 404:
                return None, "DEAD_PAGE"
            if resp.status_code in (403, 429, 503):
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
    return None, "Myntra blocked the request or returned an unexpected page."


def safe_json_loads(raw):
    try:
        return json.loads(raw)
    except Exception:
        return None


def dig(obj, *keys):
    cur = obj
    for key in keys:
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return None
    return cur


def as_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.]", "", str(value).replace(",", ""))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except Exception:
        return None


def as_int_string(value):
    if value is None:
        return "0"
    if isinstance(value, int):
        return str(value)
    text = str(value).strip().upper().replace(",", "")
    match = re.search(r"([\d.]+)", text)
    if not match:
        return "0"
    number = float(match.group(1))
    if "K" in text:
        number *= 1000
    elif "M" in text:
        number *= 1000000
    return str(int(number))


def extract_json_ld(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        data = safe_json_loads(raw)
        if not data:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type")
            if item_type == "Product":
                return item
            if isinstance(item_type, list) and "Product" in item_type:
                return item
            graph = item.get("@graph")
            if isinstance(graph, list):
                for node in graph:
                    if isinstance(node, dict) and node.get("@type") == "Product":
                        return node
    return None


def extract_embedded_json(html):
    patterns = [
        r'window\.__myx\s*=\s*({.*?})\s*;\s*</script>',
        r'window\.__INITIAL_STATE__\s*=\s*({.*?})\s*;\s*</script>',
        r'"pdpData"\s*:\s*({.*?})\s*,\s*"sellerData"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.DOTALL)
        if not match:
            continue
        payload = safe_json_loads(match.group(1))
        if payload:
            return payload
    return None


def parse_myntra(html, style_id, url):
    soup = BeautifulSoup(html, "html.parser")
    json_ld = extract_json_ld(soup)
    embedded = extract_embedded_json(html) or {}

    title = None
    brand = None
    price = None
    rating = None
    rating_count = "0"
    availability = "Unknown"
    image = None

    if json_ld:
        title = json_ld.get("name")
        brand_data = json_ld.get("brand")
        if isinstance(brand_data, dict):
            brand = brand_data.get("name")
        elif isinstance(brand_data, str):
            brand = brand_data
        offers = json_ld.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if isinstance(offers, dict):
            price = as_float(offers.get("price"))
            offer_availability = str(offers.get("availability", "")).lower()
            if "instock" in offer_availability:
                availability = "In Stock"
            elif "outofstock" in offer_availability:
                availability = "Unavailable"
        aggregate = json_ld.get("aggregateRating", {})
        if isinstance(aggregate, dict):
            rating = as_float(aggregate.get("ratingValue"))
            rating_count = as_int_string(aggregate.get("ratingCount") or aggregate.get("reviewCount"))
        image_value = json_ld.get("image")
        if isinstance(image_value, list) and image_value:
            image = image_value[0]
        elif isinstance(image_value, str):
            image = image_value

    product_data = dig(embedded, "pdpData") or embedded
    if isinstance(product_data, dict):
        title = title or product_data.get("name") or product_data.get("product")
        brand = brand or product_data.get("brand")
        price = price or as_float(
            product_data.get("discountedPrice")
            or product_data.get("price")
            or product_data.get("mrp")
        )
        rating = rating or as_float(
            product_data.get("rating")
            or dig(product_data, "ratings", "averageRating")
            or product_data.get("ratingValue")
        )
        rating_count = (
            rating_count
            if rating_count != "0"
            else as_int_string(
                product_data.get("ratingCount")
                or product_data.get("ratingsCount")
                or product_data.get("reviewCount")
            )
        )
        inventory = (
            str(product_data.get("inventoryStatus") or product_data.get("availability") or "")
            .strip()
            .lower()
        )
        if availability == "Unknown":
            if any(token in inventory for token in ["in stock", "instock", "available"]):
                availability = "In Stock"
            elif any(token in inventory for token in ["out of stock", "outofstock", "unavailable"]):
                availability = "Unavailable"
        image = image or product_data.get("searchImage") or product_data.get("defaultPicture")

    if not title:
        meta_title = soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="title"]')
        if meta_title:
            title = meta_title.get("content", "").strip() or None

    if price is None:
        meta_price = soup.select_one('meta[property="product:price:amount"]')
        if meta_price:
            price = as_float(meta_price.get("content"))

    if not image:
        meta_image = soup.select_one('meta[property="og:image"]')
        if meta_image:
            image = meta_image.get("content", "").strip() or None

    if availability == "Unknown":
        page_text = soup.get_text(" ", strip=True).lower()
        if "sold out" in page_text or "out of stock" in page_text:
            availability = "Unavailable"
        elif price is not None:
            availability = "In Stock"

    if availability == "Unavailable":
        price = None

    return {
        "asin": str(style_id),
        "styleid": str(style_id),
        "url": url,
        "title": title,
        "brand": brand,
        "price": price,
        "currency": "INR",
        "rating": rating,
        "rating_count": rating_count,
        "availability": availability,
        "image": image,
        "status": 200,
    }


def build_product_url(style_id, product_path=None):
    product_path = (product_path or DEFAULT_PRODUCT_PATH).strip("/")
    return f"https://www.myntra.com/{product_path}/{style_id}/buy"


def scrape(style_id, product_path=None, product_url=None):
    try:
        style_id = str(style_id).strip()
        if not re.match(r"^\d+$", style_id):
            return {"error": "Invalid styleid.", "status": 400}
        url = product_url or build_product_url(style_id, product_path)
        html, err = fetch_with_retry(url, max_attempts=4)
        if err == "DEAD_PAGE":
            return {"error": "Product not found or delisted.", "status": 404, "dead": True}
        if err:
            return {"error": err, "status": 422}
        data = parse_myntra(html, style_id, url)
        if not data["title"] and data["price"] is None:
            return {"error": "Could not extract Myntra product data. Try again.", "status": 422}
        return data
    except Exception as exc:
        return {"error": str(exc), "status": 500}


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
            style_id = params.get("style_id", params.get("styleid", [""]))[0].strip()
            product_path = unquote(params.get("product_path", [DEFAULT_PRODUCT_PATH])[0]).strip()
            product_url = unquote(params.get("url", [""])[0]).strip()

            if not style_id:
                self.send_json({"error": "Missing style_id.", "status": 400})
                return

            if product_url and not product_url.startswith("https://www.myntra.com/"):
                self.send_json({"error": "Only myntra.com URLs are supported.", "status": 400})
                return

            self.send_json(
                scrape(
                    style_id=style_id,
                    product_path=product_path or DEFAULT_PRODUCT_PATH,
                    product_url=product_url or None,
                )
            )
        except Exception as exc:
            self.send_json({"error": str(exc), "status": 500})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass
