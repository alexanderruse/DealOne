"""Cancel window, suspicious-price pause, and review-and-resume."""
from datetime import timedelta

import pytest

from app.ai import execute_tool
from app.config import settings
from app.models import MerchantOrder, Notification, Order, PaymentAuthorization, Permission, Product, utcnow
from app.schemas import PermissionReference
from app.services import PurchaseRejected, cancel_order, complete_order, prepare_order, resume_after_review
from sqlalchemy import select
from tests.test_nessie_payments import active, nessie  # noqa: F401  (fixture)


def bought(db):
    permission = active(db)
    order = prepare_order(db, permission.id)
    complete_order(db, order.id)
    db.commit()
    return permission, order


def test_cancel_inside_the_window_reverses_everything(db):
    stock_before = db.get(Product, "headphones").stock
    permission, order = bought(db)
    assert db.get(Product, "headphones").stock == stock_before - 1
    cancel_order(db, order.id, "owner")
    db.commit()
    db.expire_all()
    assert db.get(Order, order.id).status == "cancelled"
    assert db.get(Permission, permission.id).status == "cancelled"
    assert db.get(MerchantOrder, order.id) is None
    assert db.get(PaymentAuthorization, order.id).status == "voided"
    assert db.get(Product, "headphones").stock == stock_before


def test_cancel_is_refused_after_the_window_and_for_other_users(db):
    permission, order = bought(db)
    with pytest.raises(PurchaseRejected, match="not found"):
        cancel_order(db, order.id, "someone-else")
    db.rollback()
    order = db.get(Order, order.id)
    order.updated_at = utcnow() - timedelta(seconds=settings().cancel_window_seconds + 1)
    db.commit()
    with pytest.raises(PurchaseRejected, match="window has closed"):
        cancel_order(db, order.id, "owner")
    db.rollback()
    assert db.get(Order, order.id).status == "completed"


def test_cancel_voids_the_pending_nessie_charge(db, nessie):
    permission, order = bought(db)
    assert not any(path.endswith("/settle") for _, path, _ in nessie.requests)
    assert nessie.transactions[0]["status"] == "pending"
    cancel_order(db, order.id, "owner")
    db.commit()
    assert nessie.transactions[0]["status"] == "cancelled"
    assert db.get(Order, order.id).status == "cancelled"


def test_cancel_after_the_bank_settled_changes_nothing(db, nessie):
    permission, order = bought(db)
    nessie.transactions[0]["status"] = "completed"
    with pytest.raises(PurchaseRejected, match="Too late"):
        cancel_order(db, order.id, "owner")
    db.rollback()
    assert db.get(Order, order.id).status == "completed"
    assert db.get(MerchantOrder, order.id) is not None


def test_price_far_below_history_pauses_for_review(db):
    permission = active(db)
    product = db.get(Product, "headphones")
    product.price_cents = 5000
    db.commit()
    assert prepare_order(db, permission.id) is None
    db.commit()
    assert db.get(Permission, permission.id).status == "paused"
    assert db.scalar(select(Order).where(Order.permission_id == permission.id)) is None
    assert db.scalar(select(Notification).where(Notification.event_key == f"suspicious:{permission.id}"))


def test_normal_demo_drop_is_not_flagged(db):
    permission = active(db)
    db.get(Product, "headphones").price_cents = 15900
    db.commit()
    assert prepare_order(db, permission.id) is not None


def test_only_the_user_can_clear_a_flag_and_grok_cannot_bypass_it(db):
    permission = active(db)
    db.get(Product, "headphones").price_cents = 5000
    db.commit()
    result = execute_tool("request_purchase", PermissionReference(permission_id=permission.id), db, "owner")
    db.commit()
    assert result["status"] == "paused_for_review"
    with pytest.raises(PurchaseRejected):
        resume_after_review(db, permission.id, "someone-else")
    db.rollback()
    resume_after_review(db, permission.id, "owner")
    db.commit()
    order = prepare_order(db, permission.id)
    assert order is not None and order.total_cents < 10000


def test_cancel_and_resume_routes(client, db):
    headers = {"x-worker-token": settings().worker_token}
    expires = (utcnow() + timedelta(days=1)).isoformat()
    created = client.post("/api/permissions", json={"product_id": "headphones", "max_total_cents": 25000,
                                                     "expires_at": expires}).json()
    client.post(f"/api/permissions/{created['id']}/activate")
    order = client.post("/api/purchases", json={"permission_id": created["id"]}).json()
    assert client.post(f"/api/storefront/orders/{order['id']}", headers=headers).json()["status"] == "completed"
    listed = client.get("/api/orders").json()[0]
    assert listed["cancellable_until"] and listed["payment_reference"].startswith("sim_")
    assert client.post(f"/api/orders/{order['id']}/cancel").json()["status"] == "cancelled"
    assert client.get("/api/orders").json()[0]["cancellable_until"] is None
    assert client.post(f"/api/orders/{order['id']}/cancel").status_code == 409

    flagged = client.post("/api/permissions", json={"product_id": "speaker", "max_total_cents": 25000,
                                                     "expires_at": expires}).json()
    client.post(f"/api/permissions/{flagged['id']}/activate")
    db.get(Product, "speaker").price_cents = 2000
    db.commit()
    assert client.post("/api/purchases", json={"permission_id": flagged["id"]}).status_code == 409
    assert client.post(f"/api/permissions/{flagged['id']}/resume").json()["status"] == "active"
    assert client.post("/api/purchases", json={"permission_id": flagged["id"]}).status_code == 202
