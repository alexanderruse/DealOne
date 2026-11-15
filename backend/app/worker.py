import json
import logging
import time
from datetime import timedelta
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from sqlalchemy import select, text
from .config import settings
from .db import SessionLocal
from .models import Permission, Product, Order, MerchantOrder, Heartbeat, PriceObservation, utcnow
from .services import prepare_order, reconcile_order, notify, settle_due_charges, PurchaseRejected, total

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("dealone.worker")


def event(name, **fields):
    log.info(json.dumps({"event": name, **fields}))


def scan():
    with SessionLocal.begin() as db:
        db.merge(Heartbeat(id="worker", updated_at=utcnow()))
        for product in db.scalars(select(Product).where(Product.source_kind == "demo")):
            db.add(PriceObservation(product_id=product.id, price_cents=product.price_cents, total_cents=total(product)))
        permissions = list(db.scalars(select(Permission).where(Permission.status == "active")))
        ids = [p.id for p in permissions]
    for permission_id in ids:
        try:
            with SessionLocal.begin() as db:
                permission = db.scalar(select(Permission).where(Permission.id == permission_id).with_for_update())
                if permission.status != "active":
                    continue
                if permission.expires_at <= utcnow():
                    permission.status = "expired"
                    notify(db, permission.owner, f"expired:{permission.id}", "Your purchase permission expired without a purchase.")
                    continue
                product = db.get(Product, permission.product_id)
                if product.source_kind == "merchant":
                    if product.price_checked_at and product.price_checked_at > utcnow() - timedelta(minutes=15) and product.price_cents <= permission.max_total_cents:
                        notify(db, permission.owner, f"merchant:{permission.id}:{product.price_cents}",
                               f"Price alert: {product.name} is ${product.price_cents/100:.2f} at {product.seller}. Tax, shipping and availability need confirmation at the store. Open the listing to review.")
                    continue
                if total(product) <= permission.max_total_cents:
                    if permission.purchase_mode == "ask_first":
                        # Alert once; never purchase until the user explicitly approves.
                        notify(db, permission.owner, f"offer:{permission.id}",
                               f"Price matched your rules: {product.name} is ${total(product)/100:.2f} all in. "
                               "Approve the purchase when you are ready.")
                    else:
                        prepare_order(db, permission.id)
        except PurchaseRejected as exc:
            event("purchase_not_eligible", permission_id=permission_id, reason=str(exc))


def browser_checkout(order_id):
    # All browser traffic is confined to our controlled storefront origin.
    base = settings().storefront_url.rstrip("/")
    origin = urlparse(base).netloc
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(extra_http_headers={"x-worker-token": settings().worker_token})
        context.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).netloc == origin else route.abort())
        page = context.new_page()
        page.set_default_timeout(20_000)
        response = page.goto(f"{base}/api/storefront/checkout/{order_id}")
        if not response or not response.ok:
            raise RuntimeError("Storefront checkout unavailable")
        page.locator("#submit-order").click()
        page.wait_for_function("document.querySelector('#result').dataset.status !== undefined")
        status = page.locator("#result").get_attribute("data-status")
        result = json.loads(page.locator("#result").inner_text())
        browser.close()
        if status != "completed":
            raise PurchaseRejected(result.get("detail", "Checkout rejected"))


def process_order(order_id):
    # A dedicated DB connection holds a session advisory lock through browser execution.
    # Process death releases it; another worker can safely reconcile the merchant ledger.
    with SessionLocal() as lock_db:
        acquired = lock_db.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:id, 0))"), {"id": order_id})
        if not acquired:
            return
        try:
            with SessionLocal.begin() as db:
                order = db.get(Order, order_id)
                merchant = db.get(MerchantOrder, order_id)
                if merchant:
                    reconcile_order(db, order, merchant)
                    return
                if order.status not in ("pending", "submitting", "uncertain"):
                    return
                order.status = "submitting"
                order.updated_at = utcnow()
            try:
                browser_checkout(order_id)
                event("purchase_completed", order_id=order_id, payment_mode="simulation")
            except Exception as exc:
                with SessionLocal.begin() as db:
                    order = db.get(Order, order_id)
                    merchant = db.get(MerchantOrder, order_id)
                    if merchant:
                        reconcile_order(db, order, merchant)
                    elif order.status == "declined":
                        # Storefront already recorded the bank decline; do not overwrite it.
                        event("purchase_declined", order_id=order_id)
                    else:
                        # Unknown browser failures require review instead of blindly repeating payment.
                        order.status = "rejected" if isinstance(exc, PurchaseRejected) else "needs_action"
                        order.error = str(exc)[:500]
                        order.updated_at = utcnow()
                        permission = db.get(Permission, order.permission_id)
                        if permission.status == "active":
                            permission.status = "paused"
                        notify(db, order.owner, f"action:{order.id}", f"Purchase paused: {order.error}")
                        event("purchase_paused", order_id=order_id, reason=type(exc).__name__)
        finally:
            lock_db.execute(text("SELECT pg_advisory_unlock(hashtextextended(:id, 0))"), {"id": order_id})


def tick():
    scan()
    with SessionLocal() as db:
        ids = list(db.scalars(select(Order.id).where(Order.status.in_(["pending", "submitting", "uncertain"]))))
    for order_id in ids:
        with SessionLocal.begin() as db:
            db.merge(Heartbeat(id="worker", updated_at=utcnow()))
        process_order(order_id)
    with SessionLocal.begin() as db:
        settled = settle_due_charges(db)
        db.merge(Heartbeat(id="worker", updated_at=utcnow()))
    for order_id in settled:
        event("purchase_settled", order_id=order_id)


def main():
    settings().validate_runtime()
    from .catalog import refresh_catalog
    last_catalog_refresh = -300.0
    while True:
        try:
            if time.monotonic() - last_catalog_refresh >= 300:
                last_catalog_refresh = time.monotonic()
                refresh_catalog()
            tick()
        except Exception:
            log.exception(json.dumps({"event": "worker_tick_failed"}))
        time.sleep(settings().worker_interval_seconds)


if __name__ == "__main__":
    main()
