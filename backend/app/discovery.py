"""Bounded public-store discovery. One request per store per 65 seconds across pods."""
import hashlib
from datetime import timedelta
from decimal import Decimal, DecimalException
from urllib.parse import urlparse

import httpx
from sqlalchemy import select, text, func

from .catalog import (STORES, image_url, parse_product, fetch_feed, public_products,
                      store_product_url, validate_handle, retry_delay)
from .config import settings
from .db import SessionLocal
from .models import CatalogTask, Heartbeat, Product, Permission, PriceObservation, utcnow
from .services import product_view


def task_key(store, kind, target):
    return hashlib.sha256(f"{store}:{kind}:{target}".encode()).hexdigest()


def enqueue(db, store, kind, target, priority=100):
    if store not in STORES or kind not in ("search", "product"):
        raise ValueError("Unsupported approved-store request")
    target = validate_handle(target) if kind == "product" else " ".join(target.lower().split())[:150]
    if len(target) < 2:
        raise ValueError("Enter a product name or a supported product URL")
    key = task_key(store, kind, target)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
    task = db.get(CatalogTask, key)
    now = utcnow()
    ttl = 600 if kind == "search" else 150
    if task and task.status == "complete" and task.checked_at and task.checked_at > now-timedelta(seconds=ttl):
        return task
    if not task:
        pending = db.scalar(select(func.count()).select_from(CatalogTask).where(
            CatalogTask.store == store, CatalogTask.status == "queued"))
        if pending >= 50:
            raise ValueError("The store queue is full. Try again after the current checks finish.")
        task = CatalogTask(id=key, store=store, kind=kind, target=target, status="queued", priority=priority,
                           attempts=0, result={}, message="Queued for merchant verification", requested_at=now,
                           next_attempt_at=now)
        db.add(task)
    elif task.status != "queued":
        task.status = "queued"
        task.attempts = 0
        task.requested_at = now
        task.priority = priority
        # Preserve a server-mandated retry delay.
        task.next_attempt_at = max(task.next_attempt_at, now)
    else:
        task.priority = max(task.priority, priority)
    db.flush()
    return task


def task_view(db, task):
    listings = (task.result or {}).get("listings", [])
    handles = {listing["handle"] for listing in listings}
    if task.kind == "product":
        handles.add(task.target)
    products = [product_view(p) for p in public_products(db)
                if store_product_url(p.source_url)[0] == task.store
                and store_product_url(p.source_url)[1] in handles]
    gate = db.get(Heartbeat, "catalog:gate" if task.store == "keychron" else f"catalog:{task.store}")
    return {"task_id": task.id, "store": task.store, "status": task.status,
            "message": task.message, "checked_at": task.checked_at,
            "next_check_at": max(task.next_attempt_at, gate.updated_at+timedelta(seconds=65)) if gate and task.status == "queued" else task.next_attempt_at,
            "listings": listings, "products": products}


def discover(db, store, query):
    task = enqueue(db, store, "search", query)
    key = task.id
    db.commit()
    process_catalog_queue(store)
    db.expire_all()
    return task_view(db, db.get(CatalogTask, key))


def inspect(db, url):
    store, handle = store_product_url(url)
    task = enqueue(db, store, "product", handle, priority=110)
    key = task.id
    db.commit()
    process_catalog_queue(store)
    db.expire_all()
    return task_view(db, db.get(CatalogTask, key))


def category_for(name, category=""):
    value = f"{name} {category}".lower()
    if "keycap" in value: return "Keycaps", "keyboard"
    if "switch" in value: return "Switches", "package"
    if "keyboard" in value: return "Keyboard", "keyboard"
    if "mouse" in value or "mice" in value: return "Mouse", "mouse"
    if "head" in value: return "Audio", "headphones"
    if "speaker" in value: return "Audio", "speaker"
    return "Accessories", "package"


def parse_search(payload, store):
    """Search results are genuine product previews, never verified purchasable variants."""
    listings = []
    for row in payload["resources"]["results"]["products"][:6]:
        try:
            handle = validate_handle(row["handle"])
            amount = int(Decimal(str(row["price"])) * 100)
            if not 0 < amount <= 1_000_000:
                continue
            listings.append({"handle": handle, "name": row["title"][:180], "store": store,
                "seller": STORES[store]["name"], "image_url": image_url(row.get("image")),
                "price_from_cents": amount, "currency": "USD",
                "source_url": f"https://{STORES[store]['host']}/products/{handle}",
                "product_available": row.get("available") is True,
                "variant_status": "unverified"})
        except (ValueError, KeyError, TypeError, DecimalException):
            continue
    return listings


def register_observed_products(db, store):
    """Refresh watched products first. Unwatched history is retained without constant crawling."""
    now = utcnow()
    watched_ids = set(db.scalars(select(Permission.product_id).where(Permission.status == "active", Permission.expires_at > now)))
    grouped = {}
    for product in public_products(db):
        merchant, handle = store_product_url(product.source_url)
        if merchant != store:
            continue
        group = grouped.setdefault(handle, {"checked": product.price_checked_at, "watched": False})
        group["watched"] |= product.id in watched_ids
    for handle, row in grouped.items():
        key = task_key(store, "product", handle)
        task = db.get(CatalogTask, key)
        due = row["checked"] or now-timedelta(days=1)
        interval = 130 if row["watched"] else 1800
        if not task:
            task = CatalogTask(id=key, store=store, kind="product", target=handle, status="complete",
                priority=30 if row["watched"] else 0, attempts=0, result={}, message="Previously observed merchant product",
                checked_at=row["checked"], requested_at=now, next_attempt_at=due+timedelta(seconds=interval))
            db.add(task)
        if task.status == "complete" and task.next_attempt_at <= now:
            task.status = "queued"
            task.priority = 30 if row["watched"] else 0
        elif task.status == "complete" and row["watched"]:
            task.next_attempt_at = min(task.next_attempt_at, due+timedelta(seconds=130))


def merchant_client():
    return httpx.Client(timeout=8, follow_redirects=False, headers={"User-Agent": "DealOne/0.1 product monitor"})


def process_store(store):
    with SessionLocal.begin() as db:
        lock = 39410421 + list(STORES).index(store)
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:lock)"), {"lock": lock}):
            return {"status": "busy", "message": "A merchant check is already running"}
        register_observed_products(db, store)
        db.flush()
        now = utcnow()
        gate_id = "catalog:gate" if store == "keychron" else f"catalog:{store}"
        gate = db.get(Heartbeat, gate_id)
        if gate and gate.updated_at+timedelta(seconds=65) > now:
            return {"status": "scheduled", "message": "Merchant cooldown active; showing cached data", "next_refresh_at": (gate.updated_at+timedelta(seconds=65)).isoformat()}
        task = db.scalar(select(CatalogTask).where(CatalogTask.store == store, CatalogTask.status == "queued",
            CatalogTask.next_attempt_at <= now).order_by(CatalogTask.priority.desc(), CatalogTask.requested_at).limit(1).with_for_update())
        if not task:
            return {"status": "idle", "message": "No merchant checks are due"}
        db.merge(Heartbeat(id=gate_id, updated_at=now))
        try:
            with merchant_client() as client:
                if task.kind == "search":
                    response = client.get(f"https://{STORES[store]['host']}/search/suggest.json", params={
                        "q": task.target, "resources[type]": "product", "resources[limit]": "6",
                        "resources[options][unavailable_products]": "show", "currency": "USD"})
                    response.raise_for_status()
                    listings = parse_search(response.json(), store)
                    task.result = {"listings": listings}
                    for listing in listings[:2]:
                        enqueue(db, store, "product", listing["handle"], priority=50)
                    task.message = f"Found {len(listings)} real listings. Exact variants are checked separately before requesting."
                else:
                    payload = fetch_feed(client, task.target, store=store)
                    raw = payload.get("product", payload)
                    category, icon = category_for(raw["title"], raw.get("type", raw.get("product_type", "")))
                    ids = set()
                    for variant in raw["variants"][:250]:
                        record = parse_product(payload, task.target, category, icon, variant_id=variant["id"], store=store)
                        ids.add(record["id"])
                        product = db.get(Product, record["id"])
                        if product:
                            for key, value in record.items(): setattr(product, key, value)
                        else:
                            db.add(Product(**record))
                        db.add(PriceObservation(product_id=record["id"], price_cents=record["price_cents"], total_cents=record["price_cents"]))
                    for old in public_products(db):
                        if store_product_url(old.source_url) == (store, task.target) and old.id not in ids:
                            old.stock_status = "sold_out"; old.stock = 0
                    task.result = {"product_ids": sorted(ids)}
                    task.message = f"Verified {len(ids)} exact variants at {STORES[store]['name']}"
            task.status = "complete"
            task.checked_at = utcnow()
            task.attempts = 0
            task.next_attempt_at = utcnow()+timedelta(seconds=600 if task.kind == "search" else 1800)
            return {"status": "updated", "source": task.target, "message": task.message}
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, OverflowError, DecimalException) as exc:
            delay = 65
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                delay = max(65, retry_delay(exc.response))
            db.merge(Heartbeat(id=gate_id, updated_at=utcnow()+timedelta(seconds=delay-65)))
            task.attempts += 1
            task.next_attempt_at = utcnow()+timedelta(seconds=delay)
            task.status = "queued" if task.attempts < 3 else "unavailable"
            task.message = "Merchant verification delayed. Cached timestamps are unchanged; no stock is invented."
            return {"status": "unavailable", "source": task.target, "message": task.message}


def process_catalog_queue(store=None):
    if not settings().real_catalog_enabled:
        return {"status": "disabled", "message": "Merchant polling is disabled"}
    results = [process_store(key) for key in ([store] if store else STORES)]
    primary = next((row for row in results if row["status"] == "updated"), results[0])
    return {**primary, "stores": results}
