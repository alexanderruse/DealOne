"""Read-only merchant adapter. URLs are fixed, never supplied by a model or browser."""
import logging
from decimal import Decimal
from urllib.parse import urlparse

import httpx
from sqlalchemy import text

from .config import settings
from .db import SessionLocal
from .models import Product, PriceObservation, utcnow

SOURCES = (
    ("keychron-c3-pro-qmk-via-wired-mechanical-keyboard", "Keyboard", "keyboard"),
    ("keychron-m3-wireless-mouse", "Mouse", "mouse"),
)
log = logging.getLogger(__name__)


def image_url(value):
    if isinstance(value, dict):
        value = value.get("src", "")
    if not isinstance(value, str):
        return None
    if value.startswith("//"):
        value = "https:" + value
    parsed = urlparse(value)
    return value if parsed.scheme == "https" and parsed.hostname in ("cdn.shopify.com", "www.keychron.com") else None


def parse_product(payload, handle, category, icon):
    """Normalize Shopify's dollar JSON and cent JS formats without rounding floats."""
    if handle not in {row[0] for row in SOURCES}:
        raise ValueError("Unsupported merchant product")
    wrapped = "product" in payload
    product = payload.get("product", payload)
    variants = product["variants"]
    variant = next((v for v in variants if v.get("available") is True), variants[0])
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
    return dict(id=f"keychron-{int(variant['id'])}", name=product["title"][:180],
                variant=variant["title"][:100], seller="Keychron", condition="new", currency="USD",
                price_cents=price, original_cents=max(price, original), shipping_cents=0,
                tax_basis_points=0, stock=1 if variant.get("available") is True else 0,
                category=category, color="merchant", source_kind="merchant", icon=icon,
                source_url=f"https://www.keychron.com/products/{handle}?variant={int(variant['id'])}",
                image_url=image_url(variant_image), price_checked_at=utcnow())


def refresh_catalog():
    if not settings().real_catalog_enabled:
        return
    # A shared lock prevents API/worker replicas polling the merchant concurrently.
    with SessionLocal.begin() as db:
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(39410421)")):
            return
        with httpx.Client(timeout=8, follow_redirects=False) as client:
            for handle, category, icon in SOURCES:
                try:
                    response = client.get(f"https://www.keychron.com/products/{handle}.json", params={"currency": "USD"})
                    response.raise_for_status()
                    record = parse_product(response.json(), handle, category, icon)
                    existing = db.get(Product, record["id"])
                    if existing:
                        for key, value in record.items():
                            setattr(existing, key, value)
                    else:
                        db.add(Product(**record))
                    db.add(PriceObservation(product_id=record["id"], price_cents=record["price_cents"],
                                            total_cents=record["price_cents"]))
                except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                    # Keep the last checked value and timestamp. Never fabricate a fresh observation.
                    log.warning("Merchant refresh unavailable for %s", handle)
