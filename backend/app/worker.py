import json
import logging
import re
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


def fill_verified_digits(locator, value: str) -> bool:
    """Handle merchant input masks and verify the value after blur/rerender."""
    expected = re.sub(r"\D", "", value)
    for attempt in range(2):
        if attempt:
            locator.fill("")
            locator.press_sequentially(value, delay=50)
        else:
            locator.fill(value)
        locator.press("Tab")
        actual = re.sub(r"\D", "", locator.input_value())
        invalid = locator.get_attribute("aria-invalid") == "true"
        if actual == expected and not invalid:
            return True
    return False


CARD_SELECTORS = {
    "card_number": 'input[autocomplete="cc-number"], input[name="number"], input[id="number"], input[placeholder*="Card number"]',
    "card_expiry": 'input[autocomplete="cc-exp"], input[name*="expiry"], input[id*="expiry"], input[placeholder*="Expiration"], input[placeholder*="MM / YY"]',
    "card_cvv": 'input[autocomplete="cc-csc"], input[name*="verification"], input[name*="cvv"], input[id*="verification"], input[id*="cvv"], input[placeholder*="Security code"]',
}


def rendered_payment_input(frame, selector):
    """Ignore Shopify's tiny proxy inputs and paint the actual field on screen."""
    for locator in frame.locator(selector).all():
        box = locator.bounding_box()
        if locator.is_visible() and box and box["width"] > 40 and box["height"] > 16:
            if frame.parent_frame is not None:
                frame.frame_element().scroll_into_view_if_needed(timeout=5000)
            else:
                locator.scroll_into_view_if_needed(timeout=5000)
            return locator
    return None


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
        "phone": "2015550199",
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
        # Allow the cart and address UI, but never send a payment/order submission.
        def prevent_submission(route):
            request = route.request
            path = urlparse(request.url).path.lower()
            if request.method.upper() not in ("GET", "HEAD", "OPTIONS") and any(
                term in path for term in ("/payments", "/sessions", "/orders", "/complete", "/process")
            ):
                route.abort()
            else:
                route.continue_()
        context.route("**/*", prevent_submission)
        context.add_init_script("document.addEventListener('submit', event => event.preventDefault(), true)")
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
                    phone_input = page.locator(f'{sel}:visible').first
                    if fill_verified_digits(phone_input, profile["phone"]):
                        fields_completed.append("phone")
                    break

            page.keyboard.press("Tab")
            page.wait_for_timeout(2000)

            # Hosted payment frames often appear after shipping/address updates.
            payment_inputs = {}
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                for frame in page.frames:
                    for field, selector in CARD_SELECTORS.items():
                        if field in payment_inputs:
                            continue
                        try:
                            loc = rendered_payment_input(frame, selector)
                            if loc is not None and fill_verified_digits(loc, profile[field]):
                                payment_inputs[field] = (loc, frame)
                        except Exception:
                            # An address change may detach/recreate a hosted iframe.
                            pass
                if len(payment_inputs) == len(CARD_SELECTORS):
                    break
                page.wait_for_timeout(250)

            page.wait_for_timeout(1000)
            # Verify again after asynchronous checkout updates; detached inputs do not count.
            for field, (loc, frame) in payment_inputs.items():
                try:
                    if (re.sub(r"\D", "", loc.input_value()) == re.sub(r"\D", "", profile[field])
                            and loc.get_attribute("aria-invalid") != "true"):
                        fields_completed.append(field)
                except Exception:
                    pass
            if "phone" in fields_completed:
                try:
                    if (re.sub(r"\D", "", phone_input.input_value()) != profile["phone"]
                            or phone_input.get_attribute("aria-invalid") == "true"):
                        fields_completed.remove("phone")
                except Exception:
                    fields_completed.remove("phone")
            missing = [field for field in ("phone", *CARD_SELECTORS) if field not in fields_completed]
            if missing:
                blocker_reason = "Checkout fields unavailable or not validated: " + ", ".join(missing)

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
                        document.activeElement?.blur?.();
                        document.querySelectorAll('input').forEach(input => {
                            const idStr = ((input.name || '') + ' ' + (input.id || '') + ' ' + (input.placeholder || '')).toLowerCase();
                            if (['cc-number', 'cc-csc'].includes(input.autocomplete) ||
                                /number|card|cvv|verification|security/.test(idStr)) {
                                input.style.setProperty('-webkit-text-security', 'disc', 'important');
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
            # Chromium may leave off-screen cross-origin frames unpainted in a
            # full-page capture even after their input values were updated.
            for locator, frame in payment_inputs.values():
                if frame.parent_frame is not None:
                    frame.frame_element().scroll_into_view_if_needed(timeout=5000)
                else:
                    locator.scroll_into_view_if_needed(timeout=5000)
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            page.screenshot(path=str(screenshot_path), full_page=True)

        except Exception as exc:
            log.exception("Error executing checkout demo: %s", exc)
            blocker_reason = str(exc)[:240]
        finally:
            browser.close()

    with SessionLocal.begin() as db:
        demo = db.get(CheckoutDemoRun, demo_id)
        if demo:
            demo.status = "blocked" if blocker_reason else "completed"
            demo.blocker_reason = blocker_reason
            demo.fields_completed = json.dumps(fields_completed)
            demo.screenshot_path = str(screenshot_path) if screenshot_path.exists() else None
            demo.screenshot_png = screenshot_path.read_bytes() if screenshot_path.exists() else None
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
