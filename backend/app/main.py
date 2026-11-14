from contextlib import asynccontextmanager
from datetime import timedelta
from html import escape
import json
import secrets
from uuid import uuid4
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from .config import settings
from .db import get_db
from .models import (Product, Permission, Order, PriceObservation, Notification, Heartbeat,
                     PaymentAuthorization, VirtualCard, BankCharge, utcnow)
from . import payments
from .payments import PaymentUnavailable
from .schemas import (Rules, ActivateRequest, ParseRequest, AssistantRequest, PriceChange,
                      CreditLimitChange, PermissionReference)
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
    return {"status": "ok", "payments": settings().payments_mode}


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
    return {"session_id": user, "grok_configured": bool(settings().xai_api_key), "payments": settings().payments_mode}


@app.get("/api/payments/card")
def payment_card(user: str = Depends(owner), db: Session = Depends(get_db)):
    # Backward-compatible summary. Prefer /api/payments/cards for per-permission cards.
    cards = list(db.scalars(select(VirtualCard).join(Permission).where(Permission.owner == user)))
    issued = [c for c in cards if c.status == "issued"]
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
    rows = db.execute(select(Permission, VirtualCard).outerjoin(
        VirtualCard, VirtualCard.permission_id == Permission.id).where(Permission.owner == user)
        .order_by(Permission.created_at.desc()))
    return [permission_view(permission, card) for permission, card in rows]


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
    return [product_view(p) for p in db.scalars(select(Product).order_by(Product.id))]


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
        raise HTTPException(502, "Grok request failed. Check provider configuration or use the manual rule form.")


@app.post("/api/assistant")
def assistant(body: AssistantRequest, user: str = Depends(owner), db: Session = Depends(get_db)):
    from .ai import assistant
    try:
        return assistant(body.text, db, user)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(502, "Grok request failed. Your purchase rules remain enforced.")


@app.post("/api/permissions", status_code=201)
def create_permission(body: Rules, user: str = Depends(owner), db: Session = Depends(get_db)):
    product = db.get(Product, body.product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    if body.expires_at.tzinfo is None or body.expires_at <= utcnow() or body.expires_at > utcnow()+timedelta(days=30):
        raise HTTPException(422, "Choose a timezone-aware expiration within the next 30 days")
    permission = Permission(owner=user, product_id=product.id, variant=product.variant, seller=product.seller,
                            condition=product.condition, currency=product.currency, quantity=body.quantity,
                            max_total_cents=body.max_total_cents, expires_at=body.expires_at,
                            purchase_mode="ask_first", status="draft")
    db.add(permission)
    db.commit()
    return permission_view(permission)


@app.get("/api/permissions")
def permissions(user: str = Depends(owner), db: Session = Depends(get_db)):
    rows = db.execute(select(Permission, VirtualCard).outerjoin(
        VirtualCard, VirtualCard.permission_id == Permission.id).where(Permission.owner == user)
        .order_by(Permission.created_at.desc()))
    return [permission_view(permission, card) for permission, card in rows]


@app.post("/api/permissions/{permission_id}/activate")
def activate(permission_id: str, body: ActivateRequest | None = None, user: str = Depends(owner),
             db: Session = Depends(get_db)):
    body = body or ActivateRequest()
    permission = db.scalar(select(Permission).where(Permission.id == permission_id, Permission.owner == user).with_for_update())
    if not permission:
        raise HTTPException(404, "Permission not found")
    if permission.status != "draft" or permission.expires_at <= utcnow():
        raise HTTPException(409, "Only an unexpired draft can be activated")
    permission.purchase_mode = body.purchase_mode
    permission.status = "active"
    try:
        card = issue_card(db, permission)
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
                        "cancellable_until": cancel_deadline(o) if open_window else None})
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
    return [serialize(n) for n in db.scalars(select(Notification).where(Notification.owner == user).order_by(Notification.created_at.desc()).limit(50))]


@app.post("/api/demo/products/{product_id}/price", dependencies=[Depends(admin)])
def change_price(product_id: str, body: PriceChange, db: Session = Depends(get_db)):
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if not product:
        raise HTTPException(404, "Product not found")
    if product.source_kind == "merchant":
        raise HTTPException(409, "Real merchant prices can only change through the merchant feed")
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
    return f'''<!doctype html><html><head><title>DealOne Demo Checkout</title></head><body>
      <h1>Demo storefront · simulated payment</h1><h2>{escape(product.name)}</h2>
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
