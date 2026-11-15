from contextlib import asynccontextmanager
from datetime import timedelta
from html import escape
import json
from pathlib import Path
import secrets
from uuid import uuid4
from urllib.parse import urlsplit
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from .config import settings
from .db import get_db
from .models import (Product, Permission, Order, PriceObservation, Notification, Heartbeat,
                     PaymentAuthorization, VirtualCard, BankCharge,
                     ApprovedDomain, AISpendRecord, CheckoutDemoRun, utcnow)
from . import payments
from .merchant_links import approved_hosts, https_url, is_approved, normalize_host
from .payments import PaymentUnavailable
from .schemas import InspectRequest, HistoryRequest
from .schemas import (Rules, ActivateRequest, ParseRequest, AssistantRequest, PriceChange,
                      CreditLimitChange, PermissionReference, ApprovedDomainCreate, CheckoutDemoRequest)
from .services import (serialize, product_view, permission_view, issue_card, void_card, prepare_order,
                       complete_order, cancel_order, cancel_deadline, resume_after_review, PurchaseRejected, total)


@asynccontextmanager
async def lifespan(app):
    settings().validate_runtime()
    yield


app = FastAPI(title="DealOne", version="0.1.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings().session_secret, same_site="strict",
                   https_only=settings().app_env == "production", max_age=60*60*24*7)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Cookie-authenticated mutations must originate from this deployment.
    origin = request.headers.get("origin")
    worker_request = request.url.path.startswith("/api/storefront/") and secrets.compare_digest(
        request.headers.get("x-worker-token", ""), settings().worker_token)
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and not worker_request:
        from urllib.parse import urlparse
        expected = settings().app_domain
        allowed = {expected, request.headers.get("host"), "localhost:3001", "localhost:5173", "127.0.0.1:3001"} if settings().app_env != "production" else {expected}
        if urlparse(origin).netloc not in allowed:
            return JSONResponse({"detail": "Origin is not allowed"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


def owner(request: Request):
    if "owner" not in request.session:
        request.session["owner"] = str(uuid4())
    return request.session["owner"]


def admin(request: Request):
    if not secrets.compare_digest(request.headers.get("x-demo-admin-token", ""), settings().demo_admin_token):
        raise HTTPException(403, "Demo administrator token required")


def internal(request: Request):
    if not secrets.compare_digest(request.headers.get("x-worker-token", ""), settings().worker_token):
        raise HTTPException(403, "Internal worker credential required")


@app.exception_handler(PurchaseRejected)
async def rejection(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(PaymentUnavailable)
async def bank_unavailable(request, exc):
    # Fail closed: no purchase is recorded when the bank cannot confirm the charge.
    return JSONResponse({"detail": f"Bank unavailable: {exc}"}, status_code=503)


@app.get("/api/health/live")
def live():
    return {"status": "ok", "payments": settings().payments_mode, "real_catalog_enabled": settings().real_catalog_enabled}


@app.get("/api/health/ready")
def ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        raise HTTPException(503, "Database unavailable")


@app.get("/api/health/worker")
def worker_health(db: Session = Depends(get_db)):
    beat = db.get(Heartbeat, "worker")
    if not beat or beat.updated_at < utcnow() - timedelta(seconds=max(120, settings().worker_interval_seconds * 6)):
        raise HTTPException(503, "Worker heartbeat stale")
    return {"status": "ready", "last_seen": beat.updated_at}


@app.get("/api/session")
def session(user: str = Depends(owner)):
    ai_ready = bool(settings().openrouter_api_key)
    return {
        "session_id": user,
        "ai_configured": ai_ready,
        "grok_configured": ai_ready,
        "model": settings().openrouter_model,
        "payments": settings().payments_mode,
        "real_catalog_enabled": settings().real_catalog_enabled,
    }


@app.get("/api/payments/card")
def payment_card(user: str = Depends(owner), db: Session = Depends(get_db)):
    # Backward-compatible summary. Prefer /api/payments/cards for per-permission cards.
    cards = list(db.scalars(select(VirtualCard).join(Permission).where(
        Permission.owner == user, Permission.checkout_mode == "sandbox")))
    active_ids = set(db.scalars(select(Permission.id).where(Permission.owner == user, Permission.status == "active")))
    issued = [c for c in cards if c.status == "issued" and c.permission_id in active_ids]
    summary = {
        "provider": settings().payments_mode,
        "card_count": len(cards),
        "issued_count": len(issued),
        "committed_limit_cents": sum(c.limit_cents for c in issued),
    }
    if settings().payments_mode == "nessie":
        try:
            summary["settlement"] = payments.provider.card()
        except PaymentUnavailable:
            summary["settlement"] = None
    return summary


@app.get("/api/payments/cards")
def payment_cards(user: str = Depends(owner), db: Session = Depends(get_db)):
    """One virtual card per permission. Never returns payment credentials."""
    rows = db.execute(select(Permission, VirtualCard, Product).outerjoin(
        VirtualCard, VirtualCard.permission_id == Permission.id).outerjoin(
        Product, Product.id == Permission.product_id).where(Permission.owner == user)
        .order_by(Permission.created_at.desc()))
    results = []
    for permission, card, product in rows:
        results.append({**permission_view(permission, card), "product": (
            {**product_view(product), "domain_approved": is_approved(db, product.source_url)}
            if product else None)})
    return results


@app.get("/api/payments/cards/{permission_id}/verify")
def verify_payment_card(permission_id: str, user: str = Depends(owner), db: Session = Depends(get_db)):
    permission = db.get(Permission, permission_id)
    card = db.get(VirtualCard, permission_id)
    if not permission or permission.owner != user or not card:
        raise HTTPException(404, "Permission card not found")
    if settings().payments_mode != "nessie" or not card.nessie_account_id:
        raise HTTPException(409, "This card uses the local simulator, not the Nessie API")
    return {**payments.provider.card(card.nessie_account_id), "last4": card.last4, "verified_at": utcnow()}


@app.get("/api/payments/ledger")
def payment_ledger(user: str = Depends(owner), db: Session = Depends(get_db)):
    """DealOne bank-charge rows plus live Nessie sandbox transactions for this workspace."""
    charges = list(db.scalars(select(BankCharge).join(Permission).where(Permission.owner == user)
                              .order_by(BankCharge.created_at.desc())))
    mapped = [{**serialize(c), "source": "dealone"} for c in charges]
    live = []
    if settings().payments_mode == "nessie":
        account_ids = sorted({c.nessie_account_id for c in db.scalars(
            select(VirtualCard).join(Permission).where(Permission.owner == user, VirtualCard.nessie_account_id.is_not(None)))
            if c.nessie_account_id})
        if account_ids:
            try:
                live = [{**row, "source": "nessie"} for row in payments.provider.ledger(account_ids)]
            except PaymentUnavailable:
                live = []
    return {"charges": mapped, "nessie": live}


@app.get("/api/products")
def products(db: Session = Depends(get_db)):
    from .merchant_products import public_products
    allowed = approved_hosts(db)
    return [product_view(p) for p in public_products(db)
            if normalize_host(urlsplit(p.source_url).hostname) in allowed][:250]


@app.post("/api/price-history")
def histories(body: HistoryRequest, db: Session = Depends(get_db)):
    ranked = select(PriceObservation.product_id, PriceObservation.price_cents, PriceObservation.observed_at,
        func.row_number().over(partition_by=PriceObservation.product_id,
            order_by=PriceObservation.observed_at.desc()).label("rank")).where(
                PriceObservation.product_id.in_(body.product_ids)).subquery()
    rows = db.execute(select(ranked).where(ranked.c.rank <= 30).order_by(ranked.c.observed_at))
    result = {key: [] for key in body.product_ids}
    for row in rows:
        result[row.product_id].append({"price_cents": row.price_cents, "observed_at": row.observed_at})
    return result


@app.get("/api/products/{product_id}/prices")
def prices(product_id: str, db: Session = Depends(get_db)):
    return list(reversed([serialize(p) for p in db.scalars(select(PriceObservation).where(
        PriceObservation.product_id == product_id).order_by(PriceObservation.observed_at.desc()).limit(200))]))


@app.post("/api/rules/parse")
def parse(body: ParseRequest, user: str = Depends(owner), db: Session = Depends(get_db)):
    from .ai import parse_rules
    try:
        return parse_rules(body.text, db)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "AI assistant request failed. Check provider configuration or use the manual rule form.")


@app.post("/api/assistant")
def assistant(body: AssistantRequest, user: str = Depends(owner), db: Session = Depends(get_db)):
    from .ai import assistant
    try:
        inventory = {"status": "cached", "message": "Product links are verified on request"}
        result = assistant(body.text, db, user)
        tools = ", ".join(call["tool"] for call in result["tools"]) or "answered without tools"
        db.add(Notification(owner=user, event_key=f"assistant:{uuid4()}", message=f"Assistant command: {body.text[:160]} · {tools}. Inventory: {inventory['status']}."))
        db.commit()
        return {**result, "inventory": inventory}
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(502, "DealOne Shopping Assistant request failed. Your purchase rules remain enforced.")


@app.post("/api/catalog/inspect")
def inspect_product(body: InspectRequest, user: str = Depends(owner), db: Session = Depends(get_db)):
    from .discovery import inspect
    try:
        return inspect(db, body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/api/catalog/tasks/{task_id}")
def catalog_task(task_id: str, user: str = Depends(owner), db: Session = Depends(get_db)):
    from .models import CatalogTask
    from .discovery import task_view
    task = db.get(CatalogTask, task_id)
    if not task:
        raise HTTPException(404, "Catalog request not found")
    return task_view(db, task)


@app.post("/api/permissions", status_code=201)
def create_permission(body: Rules, user: str = Depends(owner), db: Session = Depends(get_db)):
    product = db.get(Product, body.product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    from .merchant_products import revalidate_product
    try:
        revalidate_product(db, product)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    if body.expires_at.tzinfo is None or body.expires_at <= utcnow() or body.expires_at > utcnow()+timedelta(days=30):
        raise HTTPException(422, "Choose a timezone-aware expiration within the next 30 days")
    permission = Permission(owner=user, product_id=product.id, variant=product.variant, seller=product.seller,
                            condition=product.condition, currency=product.currency, quantity=body.quantity,
                            max_total_cents=body.max_total_cents, expires_at=body.expires_at,
                            purchase_mode="ask_first", checkout_mode="merchant_handoff" if product.source_kind == "merchant" else "legacy", status="draft")
    db.add(permission)
    db.commit()
    return permission_view(permission)


@app.get("/api/permissions")
def permissions(user: str = Depends(owner), db: Session = Depends(get_db)):
    rows = db.execute(select(Permission, VirtualCard, Product).outerjoin(
        VirtualCard, VirtualCard.permission_id == Permission.id).outerjoin(
        Product, Product.id == Permission.product_id).where(Permission.owner == user, Permission.removed_at.is_(None))
        .order_by(Permission.created_at.desc()))
    results = []
    for permission, card, product in rows:
        results.append({**permission_view(permission, card), "product": (
            {**product_view(product), "domain_approved": is_approved(db, product.source_url)}
            if product else None)})
    return results


@app.post("/api/permissions/{permission_id}/activate")
def activate(permission_id: str, body: ActivateRequest | None = None, user: str = Depends(owner),
             db: Session = Depends(get_db)):
    body = body or ActivateRequest()
    permission = db.scalar(select(Permission).where(Permission.id == permission_id, Permission.owner == user).with_for_update())
    if not permission:
        raise HTTPException(404, "Permission not found")
    if permission.status != "draft" or permission.expires_at <= utcnow():
        raise HTTPException(409, "Only an unexpired draft can be activated")
    from .merchant_products import revalidate_product
    product = db.get(Product, permission.product_id)
    try:
        revalidate_product(db, product)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    if product.source_kind == "merchant":
        if product.id.startswith("link-") and body.checkout_mode == "sandbox":
            raise HTTPException(409, "This merchant supports price alerts only")
        permission.checkout_mode = body.checkout_mode
        if body.checkout_mode == "sandbox":
            amount = body.sandbox_total_cents or product.price_cents
            if amount > permission.max_total_cents:
                raise HTTPException(409, "Sandbox checkout total exceeds the approved maximum")
            permission.sandbox_total_cents = amount
    permission.purchase_mode = body.purchase_mode
    permission.status = "active"
    try:
        card = issue_card(db, permission) if permission.checkout_mode != "merchant_handoff" else None
    except PaymentUnavailable as exc:
        db.rollback()
        raise HTTPException(503, f"Could not issue sandbox card: {exc}") from None
    db.commit()
    return permission_view(permission, card)


@app.post("/api/permissions/{permission_id}/cancel")
def cancel(permission_id: str, user: str = Depends(owner), db: Session = Depends(get_db)):
    permission = db.scalar(select(Permission).where(Permission.id == permission_id, Permission.owner == user).with_for_update())
    if not permission:
        raise HTTPException(404, "Permission not found")
    if permission.status == "purchased":
        raise HTTPException(409, "Purchase already completed")
    permission.status = "cancelled"
    void_card(db, permission.id)
    db.commit()
    return permission_view(permission, db.get(VirtualCard, permission.id))


@app.delete("/api/permissions/{permission_id}")
def remove_watch(permission_id: str, user: str = Depends(owner), db: Session = Depends(get_db)):
    permission = db.scalar(select(Permission).where(
        Permission.id == permission_id, Permission.owner == user).with_for_update())
    if not permission:
        raise HTTPException(404, "Watch not found")
    pending_order = db.scalar(select(Order.id).where(
        Order.permission_id == permission_id, Order.status.in_(("pending", "submitting", "uncertain"))).limit(1))
    if pending_order:
        raise HTTPException(409, "Resolve the pending purchase in Activity before removing this watch.")
    if permission.status != "purchased":
        permission.status = "cancelled"
        void_card(db, permission.id)
    permission.removed_at = utcnow()
    db.commit()
    return {"removed": True}


@app.post("/api/purchases", status_code=202)
def purchase(body: PermissionReference, user: str = Depends(owner), db: Session = Depends(get_db)):
    order = prepare_order(db, body.permission_id, user)
    db.commit()
    if order is None:
        raise HTTPException(409, "Paused for review: the price is unusually low. Review it in your watchlist.")
    return serialize(order)


@app.get("/api/orders")
def orders(user: str = Depends(owner), db: Session = Depends(get_db)):
    results = []
    for o in db.scalars(select(Order).where(Order.owner == user).order_by(Order.created_at.desc())):
        authorization = db.get(PaymentAuthorization, o.id)
        charge = db.get(BankCharge, o.id)
        open_window = o.status == "completed" and cancel_deadline(o) > utcnow()
        results.append({**serialize(o), "payment_reference": authorization.token if authorization else None,
                        "bank_status": charge.status if charge else (authorization.status if authorization else None),
                        "cancellable_until": cancel_deadline(o) if open_window else None, "merchant_order_placed": False, "checkout_mode": db.get(Permission, o.permission_id).checkout_mode})
    return results


@app.post("/api/orders/{order_id}/cancel")
def cancel_purchase(order_id: str, user: str = Depends(owner), db: Session = Depends(get_db)):
    order = cancel_order(db, order_id, user)
    db.commit()
    return serialize(order)


@app.post("/api/permissions/{permission_id}/resume")
def resume(permission_id: str, user: str = Depends(owner), db: Session = Depends(get_db)):
    permission = resume_after_review(db, permission_id, user)
    db.commit()
    return permission_view(permission, db.get(VirtualCard, permission.id))


@app.get("/api/notifications")
def notifications(user: str = Depends(owner), db: Session = Depends(get_db)):
    return [serialize(n) for n in db.scalars(select(Notification).where(
        Notification.owner == user, Notification.dismissed_at.is_(None))
        .order_by(Notification.created_at.desc()).limit(50))]


@app.delete("/api/notifications/{notification_id}")
def dismiss_notification(notification_id: str, user: str = Depends(owner), db: Session = Depends(get_db)):
    notification = db.scalar(select(Notification).where(Notification.id == notification_id, Notification.owner == user))
    if not notification:
        raise HTTPException(404, "Alert not found")
    notification.dismissed_at = utcnow()
    db.commit()
    return {"dismissed": True}


@app.post("/api/demo/products/{product_id}/price", dependencies=[Depends(admin)])
def change_price(product_id: str, body: PriceChange, db: Session = Depends(get_db)):
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if not product:
        raise HTTPException(404, "Product not found")
    if product.source_kind == "merchant":
        raise HTTPException(409, "Real merchant prices can only change through the merchant feed")
    if settings().app_env != "test":
        raise HTTPException(410, "Fictional demo products are retired. Use a sandbox total on a real product instead")
    product.price_cents = body.price_cents
    if body.shipping_cents is not None:
        product.shipping_cents = body.shipping_cents
    db.add(PriceObservation(product_id=product.id, price_cents=product.price_cents, total_cents=total(product)))
    db.commit()
    return product_view(product)


@app.post("/api/demo/nessie/seed", dependencies=[Depends(admin)])
def demo_nessie_seed():
    if settings().payments_mode != "nessie":
        raise HTTPException(409, "Switch PAYMENTS_MODE to nessie before seeding the sandbox bank")
    try:
        return payments.provider.seed()
    except PaymentUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


@app.post("/api/demo/nessie/reset", dependencies=[Depends(admin)])
def demo_nessie_reset(body: CreditLimitChange | None = None, user: str = Depends(owner),
                      db: Session = Depends(get_db)):
    if settings().payments_mode != "nessie":
        raise HTTPException(409, "Nessie reset requires PAYMENTS_MODE=nessie")
    body = body or CreditLimitChange(available_cents=0)
    account_id = body.account_id
    if body.permission_id:
        card = db.get(VirtualCard, body.permission_id)
        permission = db.get(Permission, body.permission_id)
        if not card or not permission or permission.owner != user:
            raise HTTPException(404, "Permission card not found")
        account_id = card.nessie_account_id
    if not account_id:
        cards = list(db.scalars(select(VirtualCard).join(Permission).where(
            Permission.owner == user, VirtualCard.nessie_account_id.is_not(None))))
        account_id = cards[0].nessie_account_id if cards else settings().nessie_card_account_id
    if not account_id:
        raise HTTPException(404, "No Nessie account is linked yet. Activate a watch first.")
    try:
        return payments.provider.reset_account(account_id)
    except PaymentUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


@app.post("/api/demo/nessie/credit", dependencies=[Depends(admin)])
def demo_nessie_credit(body: CreditLimitChange, user: str = Depends(owner), db: Session = Depends(get_db)):
    """Lower available sandbox credit so a purchase can demonstrate a bank decline."""
    if settings().payments_mode != "nessie":
        raise HTTPException(409, "Nessie credit controls require PAYMENTS_MODE=nessie")
    account_id = body.account_id
    if body.permission_id:
        card = db.get(VirtualCard, body.permission_id)
        permission = db.get(Permission, body.permission_id)
        if not card or not permission or permission.owner != user:
            raise HTTPException(404, "Permission card not found")
        account_id = card.nessie_account_id
    if not account_id:
        raise HTTPException(404, "No Nessie account is linked to that watch")
    try:
        return payments.provider.set_available_credit(account_id, body.available_cents)
    except PaymentUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


@app.get("/api/storefront/checkout/{order_id}", response_class=HTMLResponse, dependencies=[Depends(internal)])
def checkout(order_id: str, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    permission = db.get(Permission, order.permission_id)
    product = db.get(Product, permission.product_id)
    return f'''<!doctype html><html><head><title>DealOne Sandbox Checkout</title></head><body>
      <h1>Sandbox checkout for a real product · no merchant order</h1><h2>{escape(product.name)}</h2>
      <p>{escape(product.variant)}</p><p data-testid="total">{order.total_cents}</p>
      <button id="submit-order">Place simulated order</button><pre id="result"></pre>
      <script>document.querySelector('#submit-order').onclick = async () => {{
        const response = await fetch('/api/storefront/orders/{order.id}', {{method:'POST'}});
        const result = await response.json();
        document.querySelector('#result').textContent = JSON.stringify(result);
        document.querySelector('#result').dataset.status = response.ok ? 'completed' : 'rejected';
      }};</script></body></html>'''


@app.post("/api/storefront/orders/{order_id}", dependencies=[Depends(internal)])
def submit_storefront_order(order_id: str, db: Session = Depends(get_db)):
    try:
        order = complete_order(db, order_id)
        db.commit()
        return serialize(order)
    except PurchaseRejected:
        # Persist decline / validation side effects before returning the 409.
        db.commit()
        raise


@app.get("/api/ai/budget")
def ai_budget(db: Session = Depends(get_db)):
    from .ai import get_ai_budget_summary
    return get_ai_budget_summary(db)


@app.get("/api/demo/approved-domains")
def list_approved_domains(db: Session = Depends(get_db)):
    domains = list(db.scalars(select(ApprovedDomain).order_by(ApprovedDomain.created_at.asc())))
    return [serialize(d) for d in domains]


@app.post("/api/demo/approved-domains", dependencies=[Depends(admin)])
def add_approved_domain(body: ApprovedDomainCreate, db: Session = Depends(get_db)):
    clean_domain = body.domain.strip().lower()
    if clean_domain.startswith("https://"):
        clean_domain = clean_domain[8:]
    elif clean_domain.startswith("http://"):
        clean_domain = clean_domain[7:]
    clean_domain = clean_domain.split("/")[0].strip()
    try:
        https_url(f"https://{clean_domain}/")
        if any(character in clean_domain for character in ":?#@"):
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "Invalid domain format") from None
    existing = db.scalar(select(ApprovedDomain).where(ApprovedDomain.domain == clean_domain))
    if existing:
        raise HTTPException(409, "Domain is already on the approved whitelist")
    domain_record = ApprovedDomain(
        domain=clean_domain,
        display_name=body.display_name or clean_domain,
    )
    db.add(domain_record)
    db.commit()
    return serialize(domain_record)


@app.delete("/api/demo/approved-domains/{domain_id}", dependencies=[Depends(admin)])
def delete_approved_domain(domain_id: str, db: Session = Depends(get_db)):
    domain = db.get(ApprovedDomain, domain_id)
    if not domain:
        raise HTTPException(404, "Approved domain not found")
    db.delete(domain)
    db.commit()
    return {"status": "deleted", "id": domain_id}


@app.post("/api/demo/checkout-demo")
def trigger_checkout_demo(body: CheckoutDemoRequest, background_tasks: BackgroundTasks,
                          request: Request, user: str = Depends(owner), db: Session = Depends(get_db)):
    admin_token = request.headers.get("x-demo-admin-token", "")
    is_admin = bool(admin_token) and secrets.compare_digest(admin_token, settings().demo_admin_token)
    if admin_token and not is_admin:
        raise HTTPException(403, "Demo admin token is incorrect. Enter the token for an admin preview, or clear it for a regular run.")
    permission = db.get(Permission, body.permission_id)
    if not permission:
        raise HTTPException(404, "Permission not found")
    if not is_admin and permission.owner != user:
        raise HTTPException(403, "Permission owner or demo administrator required")
    product = db.get(Product, permission.product_id)
    if not product:
        raise HTTPException(404, "Product not found")

    from .merchant_links import supports_checkout_demo
    if not supports_checkout_demo(product) or not is_approved(db, product.source_url):
        raise HTTPException(409, "Checkout demos require an approved merchant product link")

    demo_run = CheckoutDemoRun(
        permission_id=permission.id,
        owner=permission.owner,
        product_id=product.id,
        trigger_type="manual_admin" if is_admin else "manual_user",
        status="queued"
    )
    db.add(demo_run)
    db.commit()
    db.refresh(demo_run)

    from .worker import run_checkout_demo
    background_tasks.add_task(run_checkout_demo, demo_run.id)
    return serialize(demo_run)


@app.get("/api/activity/checkout-runs")
def activity_checkout_runs(user: str = Depends(owner), db: Session = Depends(get_db)):
    return checkout_run_views(db, user, owned_only=True)


@app.get("/api/demo/checkout-demos")
def list_checkout_demos(request: Request, user: str = Depends(owner), db: Session = Depends(get_db)):
    is_admin = bool(request.headers.get("x-demo-admin-token"))
    if is_admin:
        admin(request)
    return checkout_run_views(db, user, is_admin=is_admin)


@app.delete("/api/demo/checkout-demos/{demo_id}")
def delete_checkout_demo(demo_id: str, request: Request, user: str = Depends(owner), db: Session = Depends(get_db)):
    query = select(CheckoutDemoRun).where(CheckoutDemoRun.id == demo_id)
    if request.headers.get("x-demo-admin-token"):
        admin(request)
    else:
        query = query.where(CheckoutDemoRun.owner == user)
    demo = db.scalar(query.with_for_update())
    if not demo:
        raise HTTPException(404, "Checkout run not found")
    if demo.status in ("queued", "running"):
        raise HTTPException(409, "Wait for the checkout run to finish before deleting it.")
    from .worker import get_screenshot_dir
    (get_screenshot_dir() / f"{demo.id}.png").unlink(missing_ok=True)
    db.delete(demo)
    db.commit()
    return {"deleted": True}


def checkout_run_views(db, user, *, owned_only=False, is_admin=False):
    query = select(CheckoutDemoRun, CheckoutDemoRun.screenshot_png.is_not(None),
                   CheckoutDemoRun.payment_screenshot_png.is_not(None))
    if owned_only:
        query = query.where(CheckoutDemoRun.owner == user)
    else:
        query = query.where(CheckoutDemoRun.status.not_in(("queued", "running")))
    runs = db.execute(query.order_by(CheckoutDemoRun.created_at.desc()).limit(50)).all()
    results = []
    for r, stored_screenshot, stored_payment in runs:
        product = db.get(Product, r.product_id)
        card = db.get(VirtualCard, r.permission_id)
        has_screenshot = bool(stored_screenshot or (r.screenshot_path and Path(r.screenshot_path).exists()))
        fields = []
        try:
            fields = json.loads(r.fields_completed) if r.fields_completed else []
        except Exception:
            pass
        results.append({
            **serialize(r),
            "product_name": product.name if product else "Unknown",
            "product_variant": product.variant if product else "",
            "seller": product.seller if product else "",
            "card_last4": card.last4 if card else None,
            "has_screenshot": has_screenshot,
            "has_payment_screenshot": stored_payment,
            "can_delete": (is_admin or r.owner == user) and r.status not in ("queued", "running"),
            "fields_completed": fields,
        })
    return results


@app.get("/api/screenshots/{demo_id}/payment")
def get_payment_screenshot(demo_id: str, db: Session = Depends(get_db)):
    image = db.scalar(select(CheckoutDemoRun.payment_screenshot_png).where(CheckoutDemoRun.id == demo_id))
    if not image:
        raise HTTPException(404, "Payment close-up unavailable")
    return Response(content=image, media_type="image/png")


@app.get("/api/screenshots/{demo_id}")
def get_screenshot_file(demo_id: str, db: Session = Depends(get_db)):
    demo = db.get(CheckoutDemoRun, demo_id)
    if not demo:
        raise HTTPException(404, "Evidence run not found")
    if demo.screenshot_png:
        return Response(content=demo.screenshot_png, media_type="image/png")
    from .worker import get_screenshot_dir
    p = Path(demo.screenshot_path) if demo.screenshot_path else (get_screenshot_dir() / f"{demo_id}.png")
    if not p.exists():
        alt = get_screenshot_dir() / f"{demo_id}.png"
        if alt.exists():
            return FileResponse(str(alt), media_type="image/png")
        raise HTTPException(404, "Screenshot file not found on disk")
    return FileResponse(str(p), media_type="image/png")


@app.get("/api/evidence", response_class=HTMLResponse)
@app.get("/screenshots", response_class=HTMLResponse)
def view_screenshots_page(db: Session = Depends(get_db)):
    runs = db.execute(select(CheckoutDemoRun, CheckoutDemoRun.screenshot_png.is_not(None),
                             CheckoutDemoRun.payment_screenshot_png.is_not(None))
                      .where(CheckoutDemoRun.status.not_in(("queued", "running")))
                      .order_by(CheckoutDemoRun.created_at.desc()).limit(30)).all()
    cards_html = []
    for r, stored_screenshot, stored_payment in runs:
        product = db.get(Product, r.product_id)
        card = db.get(VirtualCard, r.permission_id)
        has_screenshot = bool(stored_screenshot or (r.screenshot_path and Path(r.screenshot_path).exists()))
        fields = []
        try:
            fields = json.loads(r.fields_completed) if r.fields_completed else []
        except Exception:
            pass

        limit_display = f"${r.nessie_verified_limit_cents / 100:.2f}" if r.nessie_verified_limit_cents else "Unset"
        card_digits = f"•••• {card.last4}" if (card and card.last4) else "•••• 4242"
        fields_badges = "".join([f'<span class="badge field">{escape(f)}</span>' for f in fields]) or '<span class="badge muted">none</span>'
        blocker_html = f'<div class="blocker"><strong>Blocker / Outcome:</strong> {escape(r.blocker_reason)}</div>' if r.blocker_reason else ''
        screenshot_img = (
            f'<div class="img-wrap"><a href="/api/screenshots/{r.id}" target="_blank">'
            f'<img src="/api/screenshots/{r.id}" alt="Synthetic checkout screenshot" loading="lazy" />'
            f'</a><div class="zoom-hint">Click image to expand</div></div>'
        ) if has_screenshot else '<div class="no-img">Screenshot pending or unavailable</div>'

        payment_path = f"/api/screenshots/{r.id}/payment"
        payment_img = (f'<div class="img-wrap"><a href="{payment_path}" target="_blank" rel="noopener">'
                       f'<img src="{payment_path}" alt="Synthetic payment close-up" loading="lazy" />'
                       '</a></div>') if stored_payment else '<p>Payment close-up unavailable for this run.</p>'

        trigger_label = {"price_drop": "Price Drop Auto-Trigger", "manual_admin": "Admin Manual Trigger"}.get(
            r.trigger_type, "Manual Trigger")
        trigger_class = "trigger-price" if r.trigger_type == "price_drop" else "trigger-manual"
        trigger_badge = f'<span class="badge {trigger_class}">{trigger_label}</span>'

        cards_html.append(f'''
        <article class="evidence-card">
          <div class="card-header">
            <div>
              <div class="tags">
                <span class="badge not-submitted">NOT SUBMITTED</span>
                {trigger_badge}
                <span class="badge status-{escape(r.status)}">{escape(r.status.upper())}</span>
              </div>
              <h2 class="product-title">{escape(product.name if product else "Product")}</h2>
              <p class="variant">{escape(product.variant if product else "")} · {escape(product.seller if product else "")}</p>
            </div>
            <div class="meta-right">
              <div class="limit-box">
                <span class="limit-label">Nessie Verified Limit</span>
                <span class="limit-val">{limit_display}</span>
              </div>
              <div class="card-digits">{card_digits}</div>
              <div class="time">{r.created_at.strftime("%b %d, %Y %H:%M:%S UTC")}</div>
            </div>
          </div>
          <div class="fields-section">
            <span class="fields-label">Completed Synthetic Fields:</span>
            <div class="fields-list">{fields_badges}</div>
          </div>
          {blocker_html}
          <div class="card-body">
            <h3>Checkout overview</h3>
            {screenshot_img}
            <h3>Payment close-up</h3>
            {payment_img}
          </div>
        </article>
        ''')

    cards_joined = "".join(cards_html) or '<div class="empty">No checkout demonstrations have run yet. Trigger one in Settings &gt; Demo Controls.</div>'

    html = f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DealOne · Checkout Runs</title>
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: #111827;
      --border: #1f293d;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --accent: #8b5cf6;
      --amber: #f59e0b;
      --red: #ef4444;
      --green: #10b981;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 24px;
      line-height: 1.5;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 24px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 32px;
    }}
    h1 {{ font-size: 24px; font-weight: 700; display: flex; align-items: center; gap: 10px; }}
    .subtitle {{ color: var(--text-muted); font-size: 14px; margin-top: 4px; }}
    .btn {{
      display: inline-block;
      background: #1f2937;
      color: var(--text);
      text-decoration: none;
      padding: 8px 16px;
      border-radius: 8px;
      border: 1px solid var(--border);
      font-size: 14px;
      font-weight: 500;
      transition: background 0.15s;
    }}
    .btn:hover {{ background: #374151; }}
    .evidence-grid {{ display: flex; flex-direction: column; gap: 32px; }}
    .evidence-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    }}
    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 16px;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .tags {{ display: flex; gap: 8px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }}
    .badge {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.5px;
      text-transform: uppercase;
    }}
    .badge.not-submitted {{
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.4);
    }}
    .badge.trigger-price {{ background: rgba(139, 92, 246, 0.15); color: #c4b5fd; border: 1px solid rgba(139, 92, 246, 0.3); }}
    .badge.trigger-manual {{ background: rgba(59, 130, 246, 0.15); color: #93c5fd; border: 1px solid rgba(59, 130, 246, 0.3); }}
    .badge.status-completed {{ background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }}
    .badge.status-blocked {{ background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }}
    .badge.status-running {{ background: rgba(245, 158, 11, 0.15); color: #fcd34d; border: 1px solid rgba(245, 158, 11, 0.3); }}
    .badge.field {{ background: #1f293d; color: #e5e7eb; border: 1px solid #374151; font-size: 11px; font-weight: normal; text-transform: none; }}
    .badge.muted {{ background: #1f2937; color: var(--text-muted); }}
    .product-title {{ font-size: 18px; font-weight: 600; margin-bottom: 2px; }}
    .variant {{ font-size: 14px; color: var(--text-muted); }}
    .meta-right {{ text-align: right; }}
    .limit-box {{ margin-bottom: 4px; }}
    .limit-label {{ font-size: 11px; color: var(--text-muted); display: block; }}
    .limit-val {{ font-size: 18px; font-weight: 700; color: #10b981; }}
    .card-digits {{ font-family: monospace; font-size: 13px; color: #9ca3af; }}
    .time {{ font-size: 12px; color: #6b7280; margin-top: 4px; }}
    .fields-section {{ margin: 12px 0; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
    .fields-label {{ font-size: 12px; color: var(--text-muted); }}
    .fields-list {{ display: flex; gap: 6px; flex-wrap: wrap; }}
    .blocker {{
      background: rgba(239, 68, 68, 0.1);
      border: 1px solid rgba(239, 68, 68, 0.3);
      color: #fca5a5;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 13px;
      margin: 12px 0;
    }}
    .card-body {{ margin-top: 16px; border-top: 1px solid var(--border); padding-top: 16px; }}
    .img-wrap {{
      position: relative;
      background: #000;
      border-radius: 8px;
      overflow: hidden;
      border: 1px solid var(--border);
    }}
    .img-wrap img {{ width: 100%; height: auto; max-height: 900px; object-fit: contain; display: block; image-rendering: -webkit-optimize-contrast; }}
    .zoom-hint {{
      position: absolute;
      bottom: 12px;
      right: 12px;
      background: rgba(0,0,0,0.7);
      color: #e5e7eb;
      font-size: 11px;
      padding: 4px 10px;
      border-radius: 4px;
      pointer-events: none;
    }}
    .no-img {{
      padding: 40px;
      text-align: center;
      color: var(--text-muted);
      background: #182234;
      border-radius: 8px;
    }}
    .empty {{
      text-align: center;
      padding: 60px 20px;
      color: var(--text-muted);
      font-size: 16px;
      background: var(--card-bg);
      border-radius: 12px;
      border: 1px solid var(--border);
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>DealOne · Checkout Runs</h1>
        <p class="subtitle">Autonomous Playwright browser checkout demonstrations with synthetic payment profiles and strict submission prevention.</p>
      </div>
      <div>
        <a href="/" class="btn">← Return to DealOne</a>
      </div>
    </header>
    <main class="evidence-grid">
      {cards_joined}
    </main>
  </div>
</body>
</html>'''
    return html
