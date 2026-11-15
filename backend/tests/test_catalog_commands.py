from datetime import timedelta
from unittest.mock import Mock

import httpx
from sqlalchemy import select

from app import ai, merchant_products as catalog
from app.config import settings
from app.models import Heartbeat, Product, Permission, VirtualCard, Notification, utcnow
from app.schemas import Rules, EmptyRequest
from tests.test_merchant_catalog import merchant_record


def test_refresh_is_shared_paced_and_ingests_mixed_variants(factory, monkeypatch):
    from app import discovery
    monkeypatch.setattr(settings(), "real_catalog_enabled", True)
    monkeypatch.setattr(discovery, "SessionLocal", factory)
    monkeypatch.setattr(discovery, "scrape_product_browser", Mock(side_effect=ValueError("No browser offers")))
    fetch = Mock(return_value={"title": "Keychron Keycaps", "variants": [
        {"id": 1, "title": "Black", "price": 4999, "available": False},
        {"id": 2, "title": "White", "price": 5999, "available": True}]})
    monkeypatch.setattr(discovery, "fetch_feed", fetch)
    with factory.begin() as db:
        discovery.enqueue(db, "keychron", "product", "new-keycap-set")
        discovery.enqueue(db, "keychron", "product", "silent-switches")
    assert discovery.process_catalog_queue()["status"] == "updated"
    assert fetch.call_args.args[1] == "new-keycap-set"
    assert discovery.process_catalog_queue()["status"] == "scheduled"
    assert fetch.call_count == 1
    with factory.begin() as db:
        assert {p.stock_status for p in catalog.public_products(db)} == {"in_stock", "sold_out"}
        db.get(Heartbeat, "catalog:gate").updated_at = utcnow() - timedelta(seconds=66)
    assert discovery.process_catalog_queue()["status"] == "updated"
    assert fetch.call_args.args[1] == "silent-switches"


def test_long_backoff_is_persisted_without_freshening_old_stock(factory, monkeypatch):
    from app import discovery
    monkeypatch.setattr(settings(), "real_catalog_enabled", True)
    monkeypatch.setattr(discovery, "SessionLocal", factory)
    monkeypatch.setattr(discovery, "scrape_product_browser", Mock(side_effect=ValueError("No browser offers")))
    request = httpx.Request("GET", "https://www.keychron.com/products/test.js")
    response = httpx.Response(429, request=request, headers={"Retry-After": "120"})
    monkeypatch.setattr(discovery, "fetch_feed", Mock(side_effect=httpx.HTTPStatusError("limited", request=request, response=response)))
    with factory.begin() as db:
        discovery.enqueue(db, "keychron", "product", "new-switches")
    assert discovery.process_catalog_queue()["status"] == "unavailable"
    with factory() as db:
        assert db.get(Heartbeat, "catalog:gate").updated_at > utcnow() + timedelta(seconds=50)
        assert catalog.public_products(db) == []
    assert discovery.process_catalog_queue()["status"] == "scheduled"


def test_grok_draft_requires_review_and_activity_is_owner_scoped(db):
    record = merchant_record(); db.add(Product(**record)); db.commit()
    result = ai.execute_tool("create_watch_draft", Rules(product_id=record["id"], max_total_cents=6000,
        expires_at=utcnow()+timedelta(days=1)), db, "user")
    assert result["status"] == "draft"
    assert list(db.scalars(select(VirtualCard))) == []
    assert db.get(Permission, result["id"]).owner == "user"
    db.add(Notification(owner="other", event_key="secret", message="Other person's alert")); db.commit()
    assert ai.execute_tool("get_activity", EmptyRequest(), db, "user")["alerts"] == []


def test_completed_grok_command_is_recorded_in_activity(client, monkeypatch):
    monkeypatch.setattr(ai, "assistant", lambda *args: {"message": "Link checked", "tools": [{"tool": "inspect_product", "result": []}]})
    response = client.post("/api/assistant", json={"text": "Check this Keychron product link"})
    assert response.status_code == 200
    alerts = client.get("/api/notifications").json()
    assert len(alerts) == 1 and "inspect_product" in alerts[0]["message"]
    assert client.delete(f"/api/notifications/{alerts[0]['id']}").status_code == 200
    assert client.get("/api/notifications").json() == []


def test_unapproved_shopify_merchant_cannot_be_requested(client, db):
    record = merchant_record()
    record["source_url"] = "https://unapproved.myshopify.com/products/mouse?variant=1234"
    db.add(Product(**record)); db.commit()
    response = client.post("/api/permissions", json={"product_id": record["id"], "max_total_cents": 6000,
        "expires_at": (utcnow()+timedelta(days=1)).isoformat()})
    assert response.status_code == 409
    assert client.get("/api/products").json() == []
