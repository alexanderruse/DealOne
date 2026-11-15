"""Read-only Shopify adapters. Hosts are approved in code; product paths are validated."""
import re
from email.utils import parsedate_to_datetime
from decimal import Decimal
from datetime import timedelta
from urllib.parse import urlparse, parse_qs

import httpx
from sqlalchemy import select

from .config import settings
from .models import Product, utcnow

# Legacy real-SKU examples used by isolated acceptance fixtures; runtime discovery is query driven.
SOURCES = (
    ("keychron-c3-pro-qmk-via-wired-mechanical-keyboard", "Keyboard", "keyboard"),
    ("keychron-m3-wireless-mouse", "Mouse", "mouse"),
)
STORES = {
    "keychron": {"name": "Keychron", "host": "www.keychron.com", "aliases": ("keychron.com", "www.keychron.com")},
    "epomaker": {"name": "Epomaker", "host": "epomaker.com", "aliases": ("epomaker.com", "www.epomaker.com")},
}


def store_product_url(url):
    parsed = urlparse(url)
    store = next((key for key, value in STORES.items() if parsed.hostname in value["aliases"]), None)
    match = re.fullmatch(r"/products/([a-z0-9][a-z0-9-]{0,179})/?", parsed.path)
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


def refresh_catalog():
    from .discovery import process_catalog_queue
    return process_catalog_queue()


def approved_product(product):
    if not product or product.source_kind != "merchant" or not product.source_url:
        return False
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
    return bool(approved_product(product) and product.stock_status == "in_stock"
                and product.stock > 0 and product.price_checked_at
                and product.price_checked_at > utcnow() - timedelta(seconds=180))


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
        if settings().app_env == "test":
            return  # Unit-test fixtures are never exposed by the public catalog.
        raise ValueError("This legacy demonstration item is no longer supported")
    # A verified snapshot avoids turning every UI command into a throttled merchant request.
    # Only a recent verified exact-variant snapshot may be used. Older data fails closed.
    ensure_requestable(product)
