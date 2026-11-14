"""Per-permission cards, ask-first default, and hidden-instruction demo product."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.ai import execute_tool
from app.models import Notification, Order, Permission, Product, VirtualCard, utcnow
from app.schemas import PermissionReference, SearchRequest
from app.services import (HIDDEN_INSTRUCTION_BAIT, PurchaseRejected, complete_order, prepare_order,
                          product_view)
from app.worker import scan
from tests.test_purchasing import permission


def test_activate_defaults_to_ask_first_and_issues_a_dedicated_card(client, db):
    expires = (utcnow() + timedelta(days=1)).isoformat()
    created = client.post("/api/permissions", json={"product_id": "headphones", "max_total_cents": 18000,
                                                    "expires_at": expires}).json()
    assert created["purchase_mode"] == "ask_first"
    activated = client.post(f"/api/permissions/{created['id']}/activate").json()
    assert activated["purchase_mode"] == "ask_first"
    assert activated["status"] == "active"
    assert activated["card"]["limit_cents"] == 18000
    assert activated["card"]["status"] == "issued"
    assert len(activated["card"]["last4"]) == 4
    other = client.post("/api/permissions", json={"product_id": "speaker", "max_total_cents": 12000,
                                                  "expires_at": expires}).json()
    second = client.post(f"/api/permissions/{other['id']}/activate",
                         json={"purchase_mode": "auto_buy"}).json()
    assert second["purchase_mode"] == "auto_buy"
    assert second["card"]["limit_cents"] == 12000
    assert second["card"]["last4"] != activated["card"]["last4"]
    cards = client.get("/api/payments/cards").json()
    assert len(cards) == 2
    summary = client.get("/api/payments/card").json()
    assert summary["issued_count"] == 2
    assert summary["committed_limit_cents"] == 30000


def test_ask_first_worker_notifies_without_buying(factory, db, monkeypatch):
    from app import worker
    rule = permission(db, purchase_mode="ask_first", max_total_cents=25000)
    db.get(Product, "headphones").price_cents = 15900
    db.commit()
    monkeypatch.setattr(worker, "SessionLocal", factory)
    scan()
    db.expire_all()
    assert db.scalar(select(Order).where(Order.permission_id == rule.id)) is None
    assert db.get(Permission, rule.id).status == "active"
    assert db.scalar(select(Notification).where(Notification.event_key == f"offer:{rule.id}"))
    scan()
    assert db.scalar(select(Order).where(Order.permission_id == rule.id)) is None


def test_auto_buy_worker_still_purchases(factory, db, monkeypatch):
    from app import worker
    rule = permission(db, purchase_mode="auto_buy", max_total_cents=25000)
    db.get(Product, "headphones").price_cents = 15900
    db.commit()
    monkeypatch.setattr(worker, "SessionLocal", factory)
    scan()
    db.expire_all()
    assert db.scalar(select(Order).where(Order.permission_id == rule.id)) is not None


def test_permission_card_limit_blocks_overcharge(db):
    rule = permission(db, max_total_cents=25000)
    card = db.get(VirtualCard, rule.id)
    card.limit_cents = 1000
    db.commit()
    order = prepare_order(db, rule.id)
    db.commit()
    with pytest.raises(PurchaseRejected, match="card limit"):
        complete_order(db, order.id)


def test_cancel_permission_voids_its_card(client):
    expires = (utcnow() + timedelta(days=1)).isoformat()
    created = client.post("/api/permissions", json={"product_id": "keyboard", "max_total_cents": 15000,
                                                    "expires_at": expires}).json()
    client.post(f"/api/permissions/{created['id']}/activate")
    cancelled = client.post(f"/api/permissions/{created['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["card"]["status"] == "voided"


def test_hidden_instruction_product_is_seeded_as_data(db):
    product = db.get(Product, "usb-hub")
    assert product is not None
    assert HIDDEN_INSTRUCTION_BAIT in product.description
    view = product_view(product)
    assert view["contains_hidden_instructions"] is True
    assert "Mechanical Keyboard" in view["description"]
    found = execute_tool("find_product", SearchRequest(query="USB"), db, "owner")
    assert any(item["id"] == "usb-hub" for item in found)
    # Product text must never become an executable purchase path on its own.
    with pytest.raises(PurchaseRejected, match="not found"):
        execute_tool("request_purchase", PermissionReference(permission_id="not-a-real-permission"), db, "owner")
