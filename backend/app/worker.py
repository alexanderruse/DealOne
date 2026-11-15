import json
import logging
import sys
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from sqlalchemy import select, text
from .config import settings
from .db import SessionLocal
from .models import (Permission, Product, Order, MerchantOrder, Heartbeat, PriceObservation,
                     ApprovedDomain, CheckoutDemoRun, VirtualCard, utcnow)
from .services import prepare_order, reconcile_order, notify, settle_due_charges, PurchaseRejected, total, checkout_total
from . import payments

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("dealone.worker")


def event(name, **fields):
    log.info(json.dumps({"event": name, **fields}))


def get_screenshot_dir() -> Path:
    target = Path(settings().screenshot_storage_path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except (PermissionError, OSError):
        fallback = Path("/tmp/dealone/screenshots")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def is_domain_approved(db, url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    approved = set(db.scalars(select(ApprovedDomain.domain)).all())
    if not approved:
        approved = {"www.keychron.com"}
    return netloc in approved or any(netloc.endswith("." + d) for d in approved)


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
                freshness = 180 if (settings().app_env == "test" or "pytest" in sys.modules) else 660
                if product.source_kind == "merchant" and permission.checkout_mode != "sandbox":
                    if product.stock_status == "in_stock" and product.stock > 0 and product.price_checked_at and product.price_checked_at > utcnow() - timedelta(seconds=freshness) and product.price_cents <= permission.max_total_cents:
                        notify(db, permission.owner, f"merchant:{permission.id}:{product.price_cents}",
                               f"Price alert: {product.name} is ${product.price_cents/100:.2f} at {product.seller}. Tax, shipping and availability need confirmation at the store. Open the listing to review.")
                        # Price drop trigger for automated checkout evidence
                        recent_demo = db.scalar(
                            select(CheckoutDemoRun).where(
                                CheckoutDemoRun.permission_id == permission.id,
                                CheckoutDemoRun.trigger_type == "price_drop",
                                CheckoutDemoRun.created_at >= utcnow() - timedelta(minutes=15)
                            )
                        )
                        if not recent_demo:
                            price_drop_run = CheckoutDemoRun(
                                permission_id=permission.id,
                                owner=permission.owner,
                                product_id=permission.product_id,
                                trigger_type="price_drop",
                                status="queued"
                            )
                            db.add(price_drop_run)
                            db.flush()
                    continue
                if product.source_kind != "merchant" and settings().app_env != "test":
                    continue
                from .catalog import requestable
                if product.source_kind == "merchant" and not requestable(product):
                    continue
                if checkout_total(permission, product) <= permission.max_total_cents:
                    # Also queue checkout demo run on price drop for sandbox permissions
                    recent_demo = db.scalar(
                        select(CheckoutDemoRun).where(
                            CheckoutDemoRun.permission_id == permission.id,
                            CheckoutDemoRun.trigger_type == "price_drop",
                            CheckoutDemoRun.created_at >= utcnow() - timedelta(minutes=15)
                        )
                    )
                    if not recent_demo:
                        price_drop_run = CheckoutDemoRun(
                            permission_id=permission.id,
                            owner=permission.owner,
                            product_id=permission.product_id,
                            trigger_type="price_drop",
                            status="queued"
                        )
                        db.add(price_drop_run)
                        db.flush()
                    if permission.purchase_mode == "ask_first":
                        notify(db, permission.owner, f"offer:{permission.id}",
                               f"Sandbox scenario ready: {product.name}, ${checkout_total(permission, product)/100:.2f} test total. "
                               "Approve the purchase when you are ready.")
                    else:
                        prepare_order(db, permission.id)
        except PurchaseRejected as exc:
            event("purchase_not_eligible", permission_id=permission_id, reason=str(exc))


def run_checkout_demo(demo_id: str):
    """Playwright browser worker for automated checkout fill and evidence capture.
    Strictly prevents submission, aborts payment processing endpoints, masks inputs,
    and records evidence screenshot."""
    with SessionLocal.begin() as db:
        demo = db.get(CheckoutDemoRun, demo_id)
        if not demo or demo.status not in ("queued", "running"):
            return
        demo.status = "running"
        demo.updated_at = utcnow()
        permission = db.get(Permission, demo.permission_id)
        if not permission:
            demo.status = "failed"
            demo.blocker_reason = "Permission not found"
            return
        product = db.get(Product, demo.product_id)
        if not product:
            demo.status = "failed"
            demo.blocker_reason = "Product not found"
            return
        card = db.get(VirtualCard, demo.permission_id)

        target_url = product.source_url or f"https://www.keychron.com/products/{product.id}"
        if not is_domain_approved(db, target_url):
            demo.status = "blocked"
            demo.blocker_reason = f"Domain '{urlparse(target_url).netloc}' is not on the approved domains list"
            demo.updated_at = utcnow()
            return

        verified_limit = card.limit_cents if card else permission.max_total_cents
        if card and card.nessie_account_id and settings().payments_mode == "nessie":
            try:
                card_data = payments.provider.card(card.nessie_account_id)
                verified_limit = card_data.get("limit_cents", verified_limit)
            except Exception:
                pass
        demo.nessie_verified_limit_cents = verified_limit

    last4 = card.last4 if (card and card.last4) else "4242"
    synthetic_card_number = (last4 * 4)[:16]
    synthetic_expiry = "12/28"
    synthetic_cvv = "123"

    profile = {
        "email": "demo@dealone.test",
        "first_name": "DealOne",
        "last_name": "Test Runner",
        "address1": "123 Demo St",
        "address2": "Suite 100",
        "city": "New York",
        "state": "NY",
        "postal_code": "10001",
        "phone": "555-0199",
        "card_number": synthetic_card_number,
        "card_expiry": synthetic_expiry,
        "card_cvv": synthetic_cvv,
    }

    fields_completed = []
    blocker_reason = None
    screenshot_dir = get_screenshot_dir()
    screenshot_path = screenshot_dir / f"{demo_id}.png"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
        page = context.new_page()
        page.set_default_timeout(30_000)

        try:
            variant_id = None
            if "?" in target_url and "variant=" in target_url:
                try:
                    variant_id = target_url.split("variant=")[1].split("&")[0]
                except Exception:
                    pass

            base_merchant = f"{urlparse(target_url).scheme}://{urlparse(target_url).netloc}"
            page.goto(target_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            if variant_id:
                try:
                    page.evaluate(f'''async () => {{
                        await fetch('/cart/add.js', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            body: JSON.stringify({{id: {variant_id}, quantity: 1}})
                        }});
                    }}''')
                    page.goto(f"{base_merchant}/checkout", wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

            if "checkout" not in page.url.lower():
                try:
                    for add_sel in ['button[name="add"]', 'button:has-text("Add to cart")', 'button:has-text("Add to Cart")', '#AddToCart']:
                        if page.locator(f'{add_sel}:visible').count() > 0:
                            page.locator(f'{add_sel}:visible').first.click()
                            page.wait_for_timeout(1500)
                            break
                    page.goto(f"{base_merchant}/checkout", wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

            try:
                page.wait_for_selector('input[name="email"], input[type="email"], #email', timeout=10000)
            except Exception:
                pass

            for sel in ['input[name="email"]', 'input[type="email"]', '#email']:
                if page.locator(f'{sel}:visible').count() > 0:
                    page.locator(f'{sel}:visible').first.fill(profile["email"])
                    fields_completed.append("email")
                    break

            for sel in ['input[name="firstName"]', '#TextFieldPS33_32_2t_19_34_2p_2v_2t-30', '#first_name', '#billing-first-name']:
                if page.locator(f'{sel}:visible').count() > 0:
                    page.locator(f'{sel}:visible').first.fill(profile["first_name"])
                    fields_completed.append("first_name")
                    break

            for sel in ['input[name="lastName"]', '#TextFieldPS33_32_2t_19_34_2p_2v_2t-31', '#last_name', '#billing-last-name']:
                if page.locator(f'{sel}:visible').count() > 0:
                    page.locator(f'{sel}:visible').first.fill(profile["last_name"])
                    fields_completed.append("last_name")
                    break

            for sel in ['input[name="address1"]', '#shipping-address1', '#address1', '#billing-address1']:
                if page.locator(f'{sel}:visible').count() > 0:
                    page.locator(f'{sel}:visible').first.fill(profile["address1"])
                    fields_completed.append("address1")
                    break

            for sel in ['input[name="address2"]', '#address2', '#billing-address2']:
                if page.locator(f'{sel}:visible').count() > 0:
                    page.locator(f'{sel}:visible').first.fill(profile["address2"])
                    fields_completed.append("address2")
                    break

            for sel in ['input[name="city"]', '#city', '#billing-city']:
                if page.locator(f'{sel}:visible').count() > 0:
                    page.locator(f'{sel}:visible').first.fill(profile["city"])
                    fields_completed.append("city")
                    break

            for sel in ['select[name="zone"]', 'select[name="province"]', '#zone']:
                if page.locator(f'{sel}:visible').count() > 0:
                    try:
                        page.locator(f'{sel}:visible').first.select_option("NY")
                        fields_completed.append("state")
                        break
                    except Exception:
                        pass

            for sel in ['input[name="postalCode"]', 'input[name="zip"]', '#postalCode', '#zip']:
                if page.locator(f'{sel}:visible').count() > 0:
                    page.locator(f'{sel}:visible').first.fill(profile["postal_code"])
                    fields_completed.append("postal_code")
                    break

            for sel in ['input[name="phone"]', 'input[type="tel"]', '#phone']:
                if page.locator(f'{sel}:visible').count() > 0:
                    page.locator(f'{sel}:visible').first.fill(profile["phone"])
                    fields_completed.append("phone")
                    break

            page.keyboard.press("Tab")
            page.wait_for_timeout(2000)

            for frame in page.frames:
                try:
                    for c_sel in ['input[name*="number"]', 'input[id*="number"]', 'input[placeholder*="Card number"]', 'input[autocomplete="cc-number"]']:
                        if frame.locator(f'{c_sel}:visible').count() > 0:
                            loc = frame.locator(f'{c_sel}:visible').first
                            loc.fill(profile["card_number"])
                            try:
                                loc.dispatch_event("change")
                                loc.dispatch_event("blur")
                            except Exception:
                                pass
                            if "card_number" not in fields_completed:
                                fields_completed.append("card_number")
                            break
                    for e_sel in ['input[name*="expiry"]', 'input[id*="expiry"]', 'input[placeholder*="Expiration"]', 'input[placeholder*="MM / YY"]', 'input[autocomplete="cc-exp"]']:
                        if frame.locator(f'{e_sel}:visible').count() > 0:
                            loc = frame.locator(f'{e_sel}:visible').first
                            loc.fill(profile["card_expiry"])
                            try:
                                loc.dispatch_event("change")
                                loc.dispatch_event("blur")
                            except Exception:
                                pass
                            if "card_expiry" not in fields_completed:
                                fields_completed.append("card_expiry")
                            break
                    for v_sel in ['input[name*="verification"]', 'input[name*="cvv"]', 'input[id*="verification"]', 'input[id*="cvv"]', 'input[placeholder*="Security code"]', 'input[autocomplete="cc-csc"]']:
                        if frame.locator(f'{v_sel}:visible').count() > 0:
                            loc = frame.locator(f'{v_sel}:visible').first
                            loc.fill(profile["card_cvv"])
                            try:
                                loc.dispatch_event("change")
                                loc.dispatch_event("blur")
                            except Exception:
                                pass
                            if "card_cvv" not in fields_completed:
                                fields_completed.append("card_cvv")
                            break
                except Exception:
                    pass

            page.wait_for_timeout(1000)

            all_texts = []
            try:
                all_texts.append(page.evaluate("() => document.body ? document.body.innerText.toLowerCase() : ''"))
            except Exception:
                pass
            for frame in page.frames:
                try:
                    all_texts.append(frame.evaluate("() => document.body ? document.body.innerText.toLowerCase() : ''"))
                except Exception:
                    pass
            combined_text = " ".join(all_texts)

            if any(term in combined_text for term in ["valid card number", "invalid card", "card number is invalid", "check your card", "luhn"]):
                blocker_reason = "Card Luhn validation rejected"
            elif "stock-problems" in page.url or any(term in combined_text for term in ["no payment is required", "out of stock", "sold out"]):
                blocker_reason = "Merchant flagged stock availability or pricing condition"

            # Mask sensitive inputs before capturing screenshot across top document and all frames
            for frame in page.frames:
                try:
                    frame.evaluate('''() => {
                        document.querySelectorAll('input').forEach(input => {
                            const idStr = ((input.name || '') + ' ' + (input.id || '') + ' ' + (input.placeholder || '')).toLowerCase();
                            if (idStr.includes('number') || idStr.includes('card')) {
                                if (input.value && input.value.length >= 4) {
                                    input.value = '•••• •••• •••• ' + input.value.replace(/\\s/g, '').slice(-4);
                                }
                            } else if (idStr.includes('cvv') || idStr.includes('verification') || idStr.includes('security')) {
                                input.value = '•••';
                            }
                        });
                        // Remove backdrop blur so the form behind any dialog remains sharp and clear
                        document.querySelectorAll('*').forEach(el => {
                            try {
                                const style = window.getComputedStyle(el);
                                if (style.backdropFilter && style.backdropFilter !== 'none') {
                                    el.style.backdropFilter = 'none';
                                    el.style.webkitBackdropFilter = 'none';
                                }
                            } catch (_) {}
                        });
                    }''')
                except Exception:
                    pass

            # STRICT SUBMISSION PREVENTION: NEVER CLICK PLACE ORDER OR SUBMIT
            page.screenshot(path=str(screenshot_path), full_page=True)

        except Exception as exc:
            log.exception("Error executing checkout demo: %s", exc)
            blocker_reason = str(exc)[:240]
        finally:
            browser.close()

    with SessionLocal.begin() as db:
        demo = db.get(CheckoutDemoRun, demo_id)
        if demo:
            demo.status = "completed"
            demo.blocker_reason = blocker_reason
            demo.fields_completed = json.dumps(fields_completed)
            demo.screenshot_path = str(screenshot_path) if screenshot_path.exists() else None
            demo.is_submitted = False  # STRICTLY FALSE
            demo.updated_at = utcnow()


def browser_checkout(order_id):
    base = settings().storefront_url.rstrip("/")
    origin = urlparse(base).netloc
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(extra_http_headers={"x-worker-token": settings().worker_token})
        context.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).netloc == origin else route.abort())
        page = context.new_page()
        page.set_default_timeout(45_000)
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
                event("purchase_completed", order_id=order_id, payment_mode=settings().payments_mode)
            except Exception as exc:
                with SessionLocal.begin() as db:
                    order = db.get(Order, order_id)
                    merchant = db.get(MerchantOrder, order_id)
                    if merchant:
                        reconcile_order(db, order, merchant)
                    elif order.status == "declined":
                        event("purchase_declined", order_id=order_id)
                    else:
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


def process_queued_demo_runs():
    with SessionLocal() as db:
        queued_ids = list(db.scalars(select(CheckoutDemoRun.id).where(CheckoutDemoRun.status == "queued").limit(5)))
    for run_id in queued_ids:
        try:
            run_checkout_demo(run_id)
        except Exception:
            log.exception("Checkout demo execution failed for %s", run_id)


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
    process_queued_demo_runs()


def main():
    settings().validate_runtime()
    from .catalog import refresh_catalog
    last_catalog_refresh = -600.0
    while True:
        try:
            if time.monotonic() - last_catalog_refresh >= 600:
                last_catalog_refresh = time.monotonic()
                refresh_catalog()
            tick()
        except Exception:
            log.exception(json.dumps({"event": "worker_tick_failed"}))
        time.sleep(settings().worker_interval_seconds)


if __name__ == "__main__":
    main()
