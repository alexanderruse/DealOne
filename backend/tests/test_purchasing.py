from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock
import pytest
from sqlalchemy import select, func, text
from app.models import Product, Permission, Order, MerchantOrder, PaymentAuthorization, Notification, utcnow
from app.services import prepare_order, complete_order, validate_purchase, total, PurchaseRejected, issue_card
from app.config import settings


def permission(db, **changes):
    p = db.get(Product, "headphones")
    values = dict(owner="owner", product_id=p.id, variant=p.variant, seller=p.seller,
                  condition=p.condition, currency=p.currency, quantity=1, max_total_cents=25000,
                  expires_at=utcnow()+timedelta(days=1), status="active", purchase_mode="auto_buy")
    values.update(changes)
    rule = Permission(**values)
    db.add(rule)
    db.flush()
    if rule.status == "active":
        issue_card(db, rule)
    db.commit()
    return rule


@pytest.mark.parametrize("change,reason", [
    ({"status": "draft"}, "not active"), ({"max_total_cents": 19900}, "tax and shipping"),
    ({"expires_at": utcnow()-timedelta(seconds=1)}, "expired"),
    ({"variant": "White"}, "variant"), ({"seller": "Unapproved"}, "seller"),
    ({"currency": "EUR"}, "currency"), ({"condition": "used"}, "condition"),
    ({"quantity": 2}, "one item"),
])
def test_permission_boundaries(db, change, reason):
    rule = permission(db, **change)
    with pytest.raises(PurchaseRejected, match=reason):
        prepare_order(db, rule.id)


def test_shipping_and_stock_are_revalidated(db):
    rule = permission(db, max_total_cents=22000)
    order = prepare_order(db, rule.id)
    db.commit()
    product = db.get(Product, "headphones")
    product.shipping_cents = 3000
    db.commit()
    with pytest.raises(PurchaseRejected, match="exceeds"):
        complete_order(db, order.id)
    db.rollback()
    product = db.get(Product, "headphones")
    product.shipping_cents = 0
    product.stock = 0
    db.commit()
    with pytest.raises(PurchaseRejected, match="out of stock"):
        complete_order(db, order.id)


def test_changed_quote_requires_review(db):
    rule = permission(db)
    order = prepare_order(db, rule.id)
    db.commit()
    db.get(Product, "headphones").price_cents -= 100
    db.commit()
    with pytest.raises(PurchaseRejected, match="total changed"):
        complete_order(db, order.id)


def test_cancellation_prevents_checkout(db):
    rule = permission(db)
    order = prepare_order(db, rule.id)
    db.commit()
    rule.status = "cancelled"
    db.commit()
    with pytest.raises(PurchaseRejected, match="not active"):
        complete_order(db, order.id)


def test_duplicate_submissions_charge_once(factory, db):
    rule = permission(db)
    order = prepare_order(db, rule.id)
    db.commit()
    def submit():
        with factory.begin() as session:
            return complete_order(session, order.id).confirmation
    with ThreadPoolExecutor(max_workers=4) as pool:
        confirmations = list(pool.map(lambda _: submit(), range(4)))
    assert len(set(confirmations)) == 1
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(MerchantOrder)) == 1
    assert db.scalar(select(func.count()).select_from(PaymentAuthorization)) == 1
    assert db.get(Product, "headphones").stock == 19
    assert db.get(Permission, rule.id).status == "purchased"


def test_concurrent_preparation_creates_one_order(factory, db):
    rule = permission(db)
    def prepare():
        with factory.begin() as session:
            return prepare_order(session, rule.id).id
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: prepare(), range(4)))
    assert len(set(ids)) == 1


def test_worker_recovers_committed_receipt_without_browser(factory, db, monkeypatch):
    from app import worker
    rule = permission(db)
    order = prepare_order(db, rule.id)
    complete_order(db, order.id)
    db.commit()
    # Model a restart after merchant confirmation and before local status observation.
    order.status = "submitting"
    db.commit()
    monkeypatch.setattr(worker, "SessionLocal", factory)
    browser = Mock(side_effect=AssertionError("Must reconcile first"))
    monkeypatch.setattr(worker, "browser_checkout", browser)
    worker.process_order(order.id)
    browser.assert_not_called()
    db.expire_all()
    assert db.get(Order, order.id).status == "completed"
    assert db.scalar(select(func.count()).select_from(PaymentAuthorization)) == 1


def test_timeout_pauses_without_resubmitting(factory, db, monkeypatch):
    from app import worker
    rule = permission(db)
    order = prepare_order(db, rule.id)
    db.commit()
    monkeypatch.setattr(worker, "SessionLocal", factory)
    browser = Mock(side_effect=TimeoutError("Checkout confirmation unknown"))
    monkeypatch.setattr(worker, "browser_checkout", browser)
    worker.process_order(order.id)
    worker.process_order(order.id)
    assert browser.call_count == 1
    db.expire_all()
    assert db.get(Order, order.id).status == "needs_action"
    assert db.get(Permission, rule.id).status == "paused"
    assert db.scalar(select(func.count()).select_from(PaymentAuthorization)) == 0


def test_timeout_after_commit_reconciles(factory, db, monkeypatch):
    from app import worker
    rule = permission(db)
    order = prepare_order(db, rule.id)
    db.commit()
    def submit_then_timeout(order_id):
        with factory.begin() as session:
            complete_order(session, order_id)
        raise TimeoutError("Lost response")
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(worker, "browser_checkout", submit_then_timeout)
    worker.process_order(order.id)
    db.expire_all()
    assert db.get(Order, order.id).status == "completed"
    assert db.scalar(select(func.count()).select_from(PaymentAuthorization)) == 1


def test_worker_lock_excludes_second_executor(factory, db, monkeypatch):
    from app import worker
    rule = permission(db)
    order = prepare_order(db, rule.id)
    db.commit()
    monkeypatch.setattr(worker, "SessionLocal", factory)
    browser = Mock()
    monkeypatch.setattr(worker, "browser_checkout", browser)
    with factory() as locking:
        locking.execute(text("SELECT pg_advisory_lock(hashtextextended(:id, 0))"), {"id": order.id})
        worker.process_order(order.id)
        browser.assert_not_called()
        locking.execute(text("SELECT pg_advisory_unlock(hashtextextended(:id, 0))"), {"id": order.id})


def test_session_isolation_and_no_client_budget_override(client):
    body = {"product_id": "headphones", "max_total_cents": 25000,
            "expires_at": (utcnow()+timedelta(days=1)).isoformat(), "quantity": 1}
    response = client.post("/api/permissions", json=body)
    assert response.status_code == 201
    rule = response.json()
    assert rule["status"] == "draft"
    assert client.post("/api/purchases", json={"permission_id": rule["id"]}).status_code == 409
    client.cookies.clear()
    client.get("/api/session")
    assert client.get("/api/permissions").json() == []
    assert client.post(f"/api/permissions/{rule['id']}/activate").status_code == 404
    assert client.post("/api/purchases", json={"permission_id": rule["id"], "max_total_cents": 999999}).status_code == 422


def test_internal_and_demo_routes_are_protected(client):
    assert client.get("/api/storefront/checkout/missing").status_code == 403
    assert client.post("/api/storefront/orders/missing").status_code == 403
    assert client.post("/api/demo/products/headphones/price", json={"price_cents": 100}).status_code == 403
    assert client.post("/api/permissions", json={}, headers={"Origin": "https://attacker.example"}).status_code == 403


def test_production_browser_worker_can_submit_from_internal_origin(client, db, monkeypatch):
    from tests.test_merchant_catalog import merchant_record
    from app import catalog
    record = merchant_record()
    db.add(Product(**record)); db.commit()
    rule = permission(db, product_id=record["id"], variant=record["variant"], seller="Keychron",
                      checkout_mode="sandbox", sandbox_total_cents=5000)
    monkeypatch.setattr(catalog, "revalidate_product", lambda session, product: catalog.ensure_requestable(product))
    order = prepare_order(db, rule.id)
    db.commit()
    monkeypatch.setattr(settings(), "app_env", "production")
    monkeypatch.setattr(settings(), "app_domain", "dealone.work")
    headers = {"Origin": "http://api:8000", "x-worker-token": settings().worker_token}
    response = client.post(f"/api/storefront/orders/{order.id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert client.post("/api/permissions", json={}, headers={"Origin":"http://api:8000"}).status_code == 403


def test_unconfigured_grok_is_explicit(client, monkeypatch):
    monkeypatch.setattr(settings(), "xai_api_key", "")
    response = client.post("/api/rules/parse", json={"text": "Buy headphones under $180"})
    assert response.status_code == 503
    assert "manual" in response.json()["detail"]


def test_grok_tools_enforce_ownership(db):
    from app.ai import execute_tool
    from app.schemas import PermissionReference
    rule = permission(db)
    for name in ("prepare_checkout", "request_purchase"):
        with pytest.raises(PurchaseRejected, match="not found"):
            execute_tool(name, PermissionReference(permission_id=rule.id), db, "other-user")
