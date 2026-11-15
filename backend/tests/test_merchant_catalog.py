from datetime import timedelta

import pytest
from sqlalchemy import select

from app.catalog import SOURCES, image_url, parse_product
from app.models import Product, Permission, Order, Notification, utcnow
from app import worker


def merchant_record():
    return parse_product({"product": {"title": "Keychron M3 Wireless Mouse", "variants": [
        {"id": 1234, "title": "Black / 1K", "price": "49.99", "price_currency": "USD", "available": True}],
        "images": [{"src": "https://cdn.shopify.com/mouse.png"}]}}, *SOURCES[1])


def test_feed_price_is_exact_and_variant_has_canonical_link():
    p = merchant_record()
    assert p["price_cents"] == 4999
    assert p["source_url"] == "https://www.keychron.com/products/keychron-m3-wireless-mouse?variant=1234"
    assert p["icon"] == "mouse"
    assert p["stock"] == 1
    assert p["stock_status"] == "in_stock"


def test_cent_feed_and_variant_artwork():
    p = parse_product({"title": "Keyboard", "variants": [
        {"id": 42, "title": "Red", "price": 3499, "available": True,
         "featured_image": {"src": "//cdn.shopify.com/red.png"}}]}, *SOURCES[0])
    assert p["price_cents"] == 3499
    assert p["image_url"] == "https://cdn.shopify.com/red.png"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://evil.example/product.png", "http://cdn.shopify.com/x"])
def test_untrusted_artwork_is_rejected(url):
    assert image_url(url) is None


def test_non_usd_price_is_rejected():
    with pytest.raises(ValueError, match="currency"):
        parse_product({"product": {"title": "Mouse", "variants": [
            {"id": 1, "title": "Black", "price": "49.99", "price_currency": "CAD"}]}}, *SOURCES[1])


def test_real_watch_alerts_once_and_never_queues_checkout(factory, monkeypatch):
    record = merchant_record()
    with factory.begin() as db:
        db.add(Product(**record))
        db.flush()
        db.add(Permission(id="real-watch", owner="user", product_id=record["id"], variant=record["variant"],
                          seller="Keychron", condition="new", currency="USD", quantity=1,
                          max_total_cents=6000, expires_at=utcnow()+timedelta(days=1), status="active"))
    monkeypatch.setattr(worker, "SessionLocal", factory)
    worker.scan()
    worker.scan()
    with factory() as db:
        assert list(db.scalars(select(Order))) == []
        notices = list(db.scalars(select(Notification)))
        assert len(notices) == 1
        assert "Tax, shipping" in notices[0].message


def test_real_checkout_and_admin_price_override_are_blocked(client, db, monkeypatch):
    record = merchant_record()
    db.add(Product(**record))
    db.commit()
    draft = client.post("/api/permissions", json={"product_id": record["id"], "max_total_cents": 6000,
                        "expires_at": (utcnow()+timedelta(days=1)).isoformat()}).json()
    client.post(f"/api/permissions/{draft['id']}/activate")
    response = client.post("/api/purchases", json={"permission_id": draft["id"]})
    assert response.status_code == 409
    assert "checkout is not connected" in response.json()["detail"]
    response = client.post(f"/api/demo/products/{record['id']}/price", json={"price_cents": 1},
                           headers={"x-demo-admin-token": "local-demo-admin"})
    assert response.status_code == 409


def test_stale_merchant_price_does_not_trigger_an_alert(factory, monkeypatch):
    record = merchant_record()
    record["price_checked_at"] = utcnow()-timedelta(hours=1)
    with factory.begin() as db:
        db.add(Product(**record))
        db.flush()
        db.add(Permission(owner="user", product_id=record["id"], variant=record["variant"], seller="Keychron",
                          condition="new", currency="USD", quantity=1, max_total_cents=6000,
                          expires_at=utcnow()+timedelta(days=1), status="active"))
    monkeypatch.setattr(worker, "SessionLocal", factory)
    worker.scan()
    with factory() as db:
        assert list(db.scalars(select(Notification))) == []


def test_grok_can_parse_catalog_with_real_price_timestamps(db, monkeypatch):
    from unittest.mock import Mock
    from app import ai
    from app.schemas import Rules
    record = merchant_record()
    db.add(Product(**record))
    db.commit()
    expected = Rules(product_id=record["id"], max_total_cents=4500,
                     expires_at=utcnow()+timedelta(days=1))
    chat = Mock()
    chat.parse.return_value = (None, ai.RuleExtraction(exact_product_match=True, rules=expected, explanation="Matched"))
    sdk = Mock()
    sdk.chat.create.return_value = chat
    monkeypatch.setattr(ai, "client", lambda: sdk)
    assert ai.parse_rules("Watch the Keychron mouse under $45", db) == expected
    chat.parse.return_value = (None, ai.RuleExtraction(exact_product_match=False, rules=None,
                                                      explanation="That exact product is not supported yet."))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        ai.parse_rules("Watch an unsupported model under $45", db)
    assert exc.value.status_code == 422


def test_disabling_polling_never_reintroduces_fictional_products(client, db, monkeypatch):
    from app import config
    record = merchant_record()
    db.add(Product(**record))
    db.commit()
    monkeypatch.setenv("REAL_CATALOG_ENABLED", "false")
    config.settings.cache_clear()
    payload = client.get("/api/products").json()
    assert [p["id"] for p in payload] == [record["id"]]
    assert all(p["source_kind"] == "merchant" for p in payload)
    assert client.get("/api/session").json()["real_catalog_enabled"] is False
    monkeypatch.setenv("REAL_CATALOG_ENABLED", "true")
    config.settings.cache_clear()
    enabled = client.get("/api/products").json()
    assert any(p["id"] == record["id"] for p in enabled)
    assert client.get("/api/session").json()["real_catalog_enabled"] is True
    monkeypatch.setenv("REAL_CATALOG_ENABLED", "false")
    config.settings.cache_clear()


def test_merchant_rate_limit_honors_one_bounded_retry(monkeypatch):
    import httpx
    from app import catalog
    sleeps=[]
    monkeypatch.setattr(catalog.time, "sleep", sleeps.append)
    replies=iter([httpx.Response(429,headers={"Retry-After":"3"}), httpx.Response(200,json={"variants":[]})])
    calls=[]
    def transport(request):
        calls.append(str(request.url))
        return next(replies)
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert catalog.fetch_feed(client,SOURCES[1][0]) == {"variants":[]}
    assert sleeps == [3.0]
    assert len(calls)==2 and calls[0]==calls[1]


def test_long_merchant_backoff_never_accepts_unverified_inventory(monkeypatch):
    import httpx
    from app import catalog
    from unittest.mock import Mock
    sleep=Mock();monkeypatch.setattr(catalog.time,"sleep",sleep)
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(429,headers={"Retry-After":"120"}))) as client:
        with pytest.raises(httpx.HTTPStatusError):catalog.fetch_feed(client,SOURCES[1][0])
    sleep.assert_not_called()
