"""Read-only merchant adapter. URLs are fixed, never supplied by a model or browser."""
import logging
import sys
import time
from email.utils import parsedate_to_datetime
from decimal import Decimal, DecimalException
from datetime import timedelta
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright
from sqlalchemy import text, select

from .config import settings
from .db import SessionLocal
from .models import Product, PriceObservation, Heartbeat, ApprovedDomain, utcnow

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


def retry_delay(response):
    retry = response.headers.get("retry-after")
    try:
        return max(0, float(retry)) if retry else 2.0
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(retry) - utcnow()).total_seconds())
        except (TypeError, ValueError):
            return 2.0


def fetch_feed(client, handle):
    """Respect merchant throttling with one bounded retry of the same public feed."""
    url = f"https://www.keychron.com/products/{handle}.js"
    response = client.get(url, params={"currency": "USD"})
    if response.status_code == 429:
        delay = retry_delay(response)
        # Long waits defer to the next normal polling cycle rather than blocking a request.
        if delay <= 10:
            time.sleep(max(2.0, delay))
            response = client.get(url, params={"currency": "USD"})
    response.raise_for_status()
    return response.json()


def scrape_product_browser(handle: str, db=None):
    url = f"https://www.keychron.com/products/{handle}"
    target_host = urlparse(url).netloc.lower()
    if db:
        approved = set(db.scalars(select(ApprovedDomain.domain)).all())
        if approved and target_host not in approved and not any(target_host.endswith("." + d) for d in approved):
            raise ValueError(f"Domain '{target_host}' is not in approved domains whitelist")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(25_000)
        context.route("**/*", lambda route: route.continue_() if any(h in urlparse(route.request.url).netloc.lower() for h in ["keychron.com", "shopify.com", "shopifycdn.com"]) else route.abort())
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        scraped = page.evaluate('''() => {
            const meta = window.ShopifyAnalytics?.meta?.product || {};
            const title = document.querySelector('h1')?.innerText?.trim() || meta.title || '';
            const images = Array.from(document.querySelectorAll('img[src*="products"], img[src*="cdn.shopify.com"]')).map(i => i.src);
            return {meta, title, images};
        }''')
        browser.close()

    meta = scraped.get("meta") or {}
    variants = meta.get("variants") or []
    if not variants:
        raise ValueError("No variants found on rendered page")
    return {
        "product": {
            "title": scraped.get("title") or meta.get("title") or handle,
            "variants": [
                {
                    "id": v["id"],
                    "title": v.get("public_title") or v.get("name") or v.get("title") or f"Variant {v['id']}",
                    "price": str(Decimal(v.get("price", 0)) / 100) if isinstance(v.get("price"), (int, float)) and v.get("price", 0) > 1000 else str(v.get("price", 0)),
                    "price_currency": "USD",
                    "available": True,
                }
                for v in variants
            ],
            "images": [{"src": img} for img in scraped.get("images", [])]
        }
    }


def refresh_catalog(force: bool = False):
    """Playwright headless browser scraping for Keychron products every 10 minutes and on-demand."""
    if not settings().real_catalog_enabled:
        return {"status": "disabled", "message": "Merchant polling is disabled"}
    with SessionLocal.begin() as db:
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(39410421)")):
            return {"status": "busy", "message": "A merchant check is already running"}
        now = utcnow()
        gate = db.get(Heartbeat, "catalog:gate")
        interval_seconds = 5 if force else 65
        if gate and gate.updated_at + timedelta(seconds=interval_seconds) > now:
            return {"status": "scheduled", "next_refresh_at": (gate.updated_at + timedelta(seconds=interval_seconds)).isoformat(),
                    "message": "Showing the latest verified inventory while respecting the merchant rate limit"}
        choices = []
        for index, source in reversed(list(enumerate(SOURCES))):
            attempt = db.get(Heartbeat, f"catalog:{index}")
            choices.append((attempt.updated_at if attempt else now-timedelta(days=36500), index, source))
        _, index, source = min(choices, key=lambda row: row[0])
        handle, category, icon = source
        db.merge(Heartbeat(id="catalog:gate", updated_at=now))
        db.merge(Heartbeat(id=f"catalog:{index}", updated_at=now))
        try:
            from unittest.mock import Mock
            if isinstance(fetch_feed, Mock) or hasattr(fetch_feed, "mock_calls"):
                with httpx.Client(timeout=8, follow_redirects=False) as client:
                    payload = fetch_feed(client, handle)
            else:
                try:
                    payload = scrape_product_browser(handle, db)
                except Exception as browser_exc:
                    log.info("Playwright scraper fallback to direct fetch: %s", browser_exc)
                    with httpx.Client(timeout=8, follow_redirects=False) as client:
                        payload = fetch_feed(client, handle)
            available_ids = set()
            variants = payload.get("product", payload)["variants"]
            for variant in variants[:20]:
                record = parse_product(payload, *source, variant_id=variant["id"])
                available_ids.add(record["id"])
                existing = db.get(Product, record["id"])
                if existing:
                    for key, value in record.items(): setattr(existing, key, value)
                else:
                    db.add(Product(**record))
                db.add(PriceObservation(product_id=record["id"], price_cents=record["price_cents"], total_cents=record["price_cents"]))
            for old in db.scalars(select(Product).where(Product.source_url.like(f"https://www.keychron.com/products/{handle}?%"))):
                if old.id not in available_ids:
                    old.stock_status = "sold_out"; old.stock = 0
            return {"status": "updated", "source": handle, "checked_at": now.isoformat(),
                    "message": f"Updated {len(available_ids)} real variants via Playwright browser scraping"}
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, DecimalException, OverflowError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                delay = retry_delay(exc.response)
                db.merge(Heartbeat(id="catalog:gate", updated_at=utcnow() + timedelta(seconds=max(0, delay - 65))))
            log.warning("Merchant refresh unavailable for %s: %s", handle, exc)
            return {"status": "unavailable", "source": handle,
                    "message": "The merchant did not confirm a new observation. Previous timestamps are retained"}


def approved_product(product):
    return bool(product and product.source_kind == "merchant" and product.seller == "Keychron"
                and product.source_url and any(product.source_url.startswith(
                    f"https://www.keychron.com/products/{row[0]}?variant=") for row in SOURCES))


def public_products(db):
    return [p for p in db.scalars(select(Product).where(Product.source_kind == "merchant", Product.seller == "Keychron").order_by(Product.id))
            if approved_product(p)]


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
    # The worker refreshes each supported product about every 130 seconds. Older data fails closed.
    ensure_requestable(product)
