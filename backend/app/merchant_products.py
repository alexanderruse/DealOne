"""Read-only merchant adapter. URLs are fixed, never supplied by a model or browser."""
import re
import sys
from email.utils import parsedate_to_datetime
from decimal import Decimal
from datetime import timedelta
from urllib.parse import urlparse, parse_qs

import httpx
from playwright.sync_api import sync_playwright
from sqlalchemy import select

from .config import settings
from .merchant_links import is_approved, link_id, offer_values
from .models import Product, utcnow


STORES = {
    "keychron": {"name": "Keychron", "host": "www.keychron.com", "aliases": ("keychron.com", "www.keychron.com")},
    "epomaker": {"name": "Epomaker", "host": "epomaker.com", "aliases": ("epomaker.com", "www.epomaker.com")},
}


def store_product_url(url):
    parsed = urlparse(url)
    store = next((key for key, value in STORES.items() if parsed.hostname in value["aliases"]), None)
    match = re.fullmatch(r"/(?:collections/[a-z0-9][a-z0-9-]*/)?products/([a-z0-9][a-z0-9-]{0,179})/?", parsed.path)
    if parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username or parsed.password or not store or not match:
        raise ValueError("Use a product URL from keychron.com or epomaker.com")
    return store, match.group(1)


def validate_handle(handle):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,179}", handle):
        raise ValueError("Invalid merchant product handle")
    return handle



def image_url(value):
    if isinstance(value, dict):
        value = value.get("src", "")
    if not isinstance(value, str):
        return None
    if value.startswith("//"):
        value = "https:" + value
    parsed = urlparse(value)
    return value if parsed.scheme == "https" and parsed.hostname in ("cdn.shopify.com", "www.keychron.com", "epomaker.com", "www.epomaker.com") else None


def parse_product(payload, handle, category, icon, variant_id=None, store="keychron"):
    """Normalize Shopify's dollar JSON and cent JS formats without rounding floats."""
    validate_handle(handle)
    merchant = STORES[store]
    wrapped = "product" in payload
    product = payload.get("product", payload)
    variants = product["variants"]
    variant = next((v for v in variants if str(v["id"]) == str(variant_id)), None) if variant_id else next((v for v in variants if v.get("available") is True), variants[0])
    if variant is None:
        raise ValueError("The requested merchant variant no longer exists")
    if variant.get("price_currency", "USD") != "USD":
        raise ValueError("Unsupported currency")
    scale = 100 if wrapped else 1
    price = int(Decimal(str(variant["price"])) * scale)
    original = int(Decimal(str(variant.get("compare_at_price") or variant["price"])) * scale)
    if not 0 < price <= 1_000_000:
        raise ValueError("Invalid merchant price")
    variant_image = variant.get("featured_image")
    if not variant_image:
        variant_image = next((i for i in product.get("images", []) if isinstance(i, dict)
                              and i.get("id") == variant.get("image_id")), None)
    if not variant_image:
        variant_image = next(iter(product.get("images", [])), None)
    return dict(id=f"{store}-{int(variant['id'])}", name=product["title"][:180],
                variant=variant["title"][:100], seller=merchant["name"], condition="new", currency="USD",
                price_cents=price, original_cents=max(price, original), shipping_cents=0,
                tax_basis_points=0, stock=1 if variant.get("available") is True else 0,
                stock_status="in_stock" if variant.get("available") is True else "sold_out" if variant.get("available") is False else "unknown",
                category=category, color="merchant", source_kind="merchant", icon=icon,
                source_url=f"https://{merchant['host']}/products/{handle}?variant={int(variant['id'])}",
                image_url=image_url(variant_image), price_checked_at=utcnow())


def retry_delay(response):
    retry = response.headers.get("retry-after")
    try:
        return max(0, float(retry)) if retry else 2.0
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(retry) - utcnow()).total_seconds())
        except (TypeError, ValueError):
            return 2.0


def fetch_feed(client, handle, store="keychron"):
    """One public request; the shared queue handles every retry and cooldown."""
    validate_handle(handle)
    url = f"https://{STORES[store]['host']}/products/{handle}.js"
    response = client.get(url, params={"currency": "USD"})
    response.raise_for_status()
    return response.json()


def scrape_product_browser(handle: str, db=None, store="keychron"):
    """Read exact USD variant offers from a supported store in Chromium."""
    validate_handle(handle)
    host = STORES[store]["host"]
    path = f"/products/{handle}"
    url = f"https://{host}{path}"
    if db is not None and not is_approved(db, url):
        raise ValueError("Merchant is not on the approved domains whitelist")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(service_workers="block")
            context.route("**/*", lambda route: route.continue_()
                          if route.request.is_navigation_request() and route.request.url == url
                          else route.abort())
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=12_000)
            if response is None or response.status != 200 or page.url != url:
                raise ValueError("Merchant product page unavailable")
            groups = page.locator('script[type="application/ld+json"]').evaluate_all("""nodes =>
                nodes.flatMap(node => {
                    try {
                        const document = JSON.parse(node.textContent);
                        return document?.["@type"] === "ProductGroup" ? [document] : [];
                    } catch {
                        return [];
                    }
                })
            """)
        finally:
            browser.close()

    if len(groups) != 1:
        raise ValueError("Expected one merchant product group")
    group = groups[0]
    name = group.get("name")
    rows = group.get("hasVariant")
    if not isinstance(name, str) or not name.strip() or not isinstance(rows, list) or not 0 < len(rows) <= 250:
        raise ValueError("Missing merchant variant data")

    variants = {}
    for row in rows:
        offer = row.get("offers") if isinstance(row, dict) and row.get("@type") == "Product" else None
        if not isinstance(offer, dict) or offer.get("@type") != "Offer" or not isinstance(offer.get("url"), str):
            raise ValueError("Missing merchant variant offer")
        url = urlparse(offer["url"])
        variant_ids = parse_qs(url.query).get("variant", [])
        values = offer_values(row.get("name"), offer.get("price"), offer.get("priceCurrency"), offer.get("availability"))
        if (url.scheme != "https" or url.netloc != host or url.path != path or len(variant_ids) != 1
                or not re.fullmatch(r"[1-9][0-9]{0,19}", variant_ids[0]) or values is None):
            raise ValueError("Could not verify exact merchant variant")
        variant_id = int(variant_ids[0])
        if variant_id in variants:
            raise ValueError("Duplicate merchant variant offer")
        _, cents, stock = values
        variants[variant_id] = {"id": variant_id, "title": row["name"].removeprefix(name + " - "),
                                "price": cents, "available": bool(stock), "featured_image": row.get("image")}
    return {"title": name, "variants": list(variants.values())}


def approved_product(product):
    if not product or product.source_kind != "merchant" or not product.source_url:
        return False
    if product.id.startswith("link-"):
        return product.id == link_id(product.source_url)
    try:
        store, _ = store_product_url(product.source_url)
        variant = parse_qs(urlparse(product.source_url).query).get("variant", [""])[0]
        return product.seller == STORES[store]["name"] and variant.isdigit() and product.id == f"{store}-{int(variant)}"
    except ValueError:
        return False


def public_products(db):
    return [p for p in db.scalars(select(Product).where(Product.source_kind == "merchant")
            .order_by(Product.price_checked_at.desc(), Product.id)) if approved_product(p)]


def requestable(product):
    freshness = 180 if (settings().app_env == "test" or "pytest" in sys.modules) else 660
    return bool(approved_product(product) and product.stock_status == "in_stock"
                and product.stock > 0 and product.price_checked_at
                and product.price_checked_at > utcnow() - timedelta(seconds=freshness))


def ensure_requestable(product):
    if not approved_product(product):
        raise ValueError("Only supported real merchant products can be requested")
    if product.stock_status == "sold_out":
        raise ValueError("This exact variant is sold out and cannot be requested")
    if not requestable(product):
        raise ValueError("Availability is unverified or stale. Refresh the merchant listing before requesting this item")


def revalidate_product(db, product):
    """Recheck the exact variant before accepting a request or authorizing a sandbox charge."""
    if product.source_kind != "merchant":
        if settings().app_env == "test" or "pytest" in sys.modules:
            return  # Unit-test fixtures are never exposed by the public catalog.
        raise ValueError("This legacy demonstration item is no longer supported")
    # A verified snapshot avoids turning every UI command into a throttled merchant request.
    # Only a recent verified exact-variant snapshot may be used. Older data fails closed.
    if not is_approved(db, product.source_url):
        raise ValueError("This domain is not on the approved domains whitelist")
    ensure_requestable(product)
