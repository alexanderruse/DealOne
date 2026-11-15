"""Read-only merchant adapter. URLs are fixed, never supplied by a model or browser."""
import logging
import time
from email.utils import parsedate_to_datetime
from decimal import Decimal, DecimalException
from datetime import timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import text, select

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


def parse_product(payload, handle, category, icon, variant_id=None):
    """Normalize Shopify's dollar JSON and cent JS formats without rounding floats."""
    if handle not in {row[0] for row in SOURCES}:
        raise ValueError("Unsupported merchant product")
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
    return dict(id=f"keychron-{int(variant['id'])}", name=product["title"][:180],
                variant=variant["title"][:100], seller="Keychron", condition="new", currency="USD",
                price_cents=price, original_cents=max(price, original), shipping_cents=0,
                tax_basis_points=0, stock=1 if variant.get("available") is True else 0,
                stock_status="in_stock" if variant.get("available") is True else "sold_out" if variant.get("available") is False else "unknown",
                category=category, color="merchant", source_kind="merchant", icon=icon,
                source_url=f"https://www.keychron.com/products/{handle}?variant={int(variant['id'])}",
                image_url=image_url(variant_image), price_checked_at=utcnow())


def fetch_feed(client, handle):
    """Respect merchant throttling with one bounded retry of the same public feed."""
    url = f"https://www.keychron.com/products/{handle}.js"
    response = client.get(url, params={"currency": "USD"})
    if response.status_code == 429:
        retry = response.headers.get("retry-after")
        try:
            delay = float(retry) if retry else 2.0
        except ValueError:
            try:
                delay = (parsedate_to_datetime(retry) - utcnow()).total_seconds()
            except (TypeError, ValueError):
                delay = 2.0
        # Long waits defer to the next normal polling cycle rather than blocking a request.
        if delay <= 10:
            time.sleep(max(2.0, delay))
            response = client.get(url, params={"currency": "USD"})
    response.raise_for_status()
    return response.json()


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
                    payload = fetch_feed(client, handle)
                    record = parse_product(payload, handle, category, icon)
                    # Refresh availability for previously selected variants too, including ones now sold out.
                    for old in db.scalars(select(Product).where(Product.source_url.like(f"https://www.keychron.com/products/{handle}?%"))):
                        try:
                            updated = parse_product(payload, handle, category, icon, old.id.removeprefix("keychron-"))
                            for key, value in updated.items():
                                setattr(old, key, value)
                        except ValueError:
                            old.stock_status = "sold_out"
                            old.stock = 0
                    existing = db.get(Product, record["id"])
                    if existing:
                        for key, value in record.items():
                            setattr(existing, key, value)
                    else:
                        db.add(Product(**record))
                    db.add(PriceObservation(product_id=record["id"], price_cents=record["price_cents"],
                                            total_cents=record["price_cents"]))
                except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, DecimalException, OverflowError):
                    # Keep the last checked value and timestamp. Never fabricate a fresh observation.
                    log.warning("Merchant refresh unavailable for %s", handle)


def public_products(db):
    return [p for p in db.scalars(select(Product).where(Product.source_kind == "merchant", Product.seller == "Keychron").order_by(Product.id))
            if p.source_url and any(p.source_url.startswith(f"https://www.keychron.com/products/{row[0]}?variant=") for row in SOURCES)]


def requestable(product):
    return bool(product and product.source_kind == "merchant" and product.stock_status == "in_stock"
                and product.stock > 0 and product.price_checked_at
                and product.price_checked_at > utcnow() - timedelta(minutes=15))


def ensure_requestable(product):
    if not product or product.source_kind != "merchant":
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
    if settings().app_env != "test":
        source = next((row for row in SOURCES if product.source_url and product.source_url.startswith(
            f"https://www.keychron.com/products/{row[0]}?variant=")), None)
        if source is None:
            raise ValueError("Unsupported merchant")
        try:
            with httpx.Client(timeout=8, follow_redirects=False) as client:
                payload = fetch_feed(client, source[0])
                record = parse_product(payload, *source, variant_id=product.id.removeprefix("keychron-"))
            for key, value in record.items():
                setattr(product, key, value)
            db.flush()
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, DecimalException):
            raise ValueError("The merchant could not confirm availability. No purchase request was accepted") from None
    ensure_requestable(product)
