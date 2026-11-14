"""Nessie provider tests. A fake transport stands in for the sandbox bank; no network is used."""
from datetime import timedelta
import json

import httpx
import pytest

from app import payments
from app.config import settings
from app.models import Order, PaymentAuthorization, Permission, Product, utcnow
from app.payments import NessieProvider, PaymentUnavailable
from app.services import PurchaseRejected, complete_order, prepare_order, issue_card


def active(db):
    p = db.get(Product, "headphones")
    rule = Permission(owner="owner", product_id=p.id, variant=p.variant, seller=p.seller, condition=p.condition,
                      currency=p.currency, quantity=1, max_total_cents=25000,
                      expires_at=utcnow() + timedelta(days=1), status="active", purchase_mode="auto_buy")
    db.add(rule)
    db.flush()
    issue_card(db, rule)
    db.commit()
    return rule


class FakeBank:
    """Just enough of Nessie v2 to exercise the provider: one card with a limit."""

    def __init__(self, limit_cents=100_000, fail_with=None):
        self.limit_cents = limit_cents
        self.fail_with = fail_with
        self.transactions = []
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, request.headers.get("authorization")))
        assert "key=" not in str(request.url)
        if self.fail_with:
            raise self.fail_with
        path = request.url.path
        if request.method == "GET" and path.endswith("/transactions"):
            return httpx.Response(200, json={"items": self.transactions})
        if request.method == "GET" and path == "/merchants":
            return httpx.Response(200, json={"items": [{"id": "mer_1", "name": "Demo Store"}]})
        if request.method == "POST" and path.endswith("/purchases"):
            body = json.loads(request.content)
            owed = sum(t["amount_cents"] for t in self.transactions)
            if owed + body["amount_cents"] > self.limit_cents:
                return httpx.Response(422, json={"type": "credit_limit_exceeded", "title": "Credit limit exceeded"})
            tx = {"id": f"txn_{len(self.transactions) + 1}", "kind": "purchase", "status": "pending", **body}
            self.transactions.append(tx)
            return httpx.Response(201, json=tx)
        if request.method == "POST" and path.endswith("/settle"):
            return httpx.Response(200, json={"status": "completed"})
        if request.method == "POST" and path.endswith("/cancel"):
            tx = next(t for t in self.transactions if t["id"] == path.split("/")[2])
            if tx["status"] != "pending":
                return httpx.Response(409, json={"type": "transaction_not_pending"})
            tx["status"] = "cancelled"
            return httpx.Response(200, json=tx)
        if request.method == "GET" and path.startswith("/accounts/"):
            owed = sum(t["amount_cents"] for t in self.transactions)
            return httpx.Response(200, json={"type": "Credit Card", "credit_limit_cents": self.limit_cents,
                                             "balance_cents": owed, "available_cents": self.limit_cents - owed})
        return httpx.Response(404, json={})


@pytest.fixture
def nessie(monkeypatch):
    monkeypatch.setenv("PAYMENTS_MODE", "nessie")
    monkeypatch.setenv("NESSIE_API_KEY", "nsk_test")
    monkeypatch.setenv("NESSIE_CARD_ACCOUNT_ID", "acct_card")
    settings.cache_clear()
    bank = FakeBank()
    provider = NessieProvider()
    monkeypatch.setattr(provider, "_client", lambda: httpx.Client(
        base_url="https://bank.test", headers={"Authorization": "Bearer nsk_test"},
        transport=httpx.MockTransport(bank.handler)))
    monkeypatch.setattr(payments, "provider", provider)
    yield bank
    settings.cache_clear()


def test_purchase_is_charged_to_the_nessie_card(db, nessie):
    permission = active(db)
    order = prepare_order(db, permission.id)
    completed = complete_order(db, order.id)
    db.commit()
    assert completed.status == "completed"
    authorization = db.get(PaymentAuthorization, order.id)
    assert authorization.token == "txn_1"
    assert nessie.transactions[0]["amount_cents"] == order.total_cents
    assert nessie.transactions[0]["description"] == f"DEALONE {order.id}"
    assert nessie.transactions[0]["merchant_id"] == "mer_1"
    assert all(auth == "Bearer nsk_test" for _, _, auth in nessie.requests)


def test_card_limit_decline_rejects_without_an_order(db, nessie):
    nessie.limit_cents = 1_000
    permission = active(db)
    order = prepare_order(db, permission.id)
    db.commit()
    with pytest.raises(PurchaseRejected, match="card limit"):
        complete_order(db, order.id)
    db.rollback()
    assert db.get(PaymentAuthorization, order.id) is None
    assert db.get(Order, order.id).status == "pending"
    assert nessie.transactions == []


def test_retry_reuses_the_existing_charge(db, nessie):
    permission = active(db)
    order = prepare_order(db, permission.id)
    db.commit()
    # The bank accepted the charge, but our commit was lost before the receipt was written.
    complete_order(db, order.id)
    db.rollback()
    assert len(nessie.transactions) == 1
    complete_order(db, order.id)
    db.commit()
    assert len(nessie.transactions) == 1
    assert db.get(PaymentAuthorization, order.id).token == "txn_1"


def test_unreachable_bank_fails_closed(db, nessie):
    nessie.fail_with = httpx.ConnectError("down")
    permission = active(db)
    order = prepare_order(db, permission.id)
    db.commit()
    with pytest.raises(PaymentUnavailable):
        complete_order(db, order.id)
    db.rollback()
    assert db.get(PaymentAuthorization, order.id) is None
    assert db.get(Permission, permission.id).status == "active"


def test_storefront_route_maps_bank_answers_to_http(client, db, nessie):
    headers = {"x-worker-token": settings().worker_token}
    permission = active(db)
    order = prepare_order(db, permission.id)
    db.commit()
    nessie.fail_with = httpx.ConnectError("down")
    assert client.post(f"/api/storefront/orders/{order.id}", headers=headers).status_code == 503
    nessie.fail_with = None
    nessie.limit_cents = 1_000
    declined = client.post(f"/api/storefront/orders/{order.id}", headers=headers)
    assert declined.status_code == 409 and "card limit" in declined.json()["detail"]
    nessie.limit_cents = 100_000
    assert client.post(f"/api/storefront/orders/{order.id}", headers=headers).json()["status"] == "completed"


def test_card_view_and_mode_are_reported(client, nessie):
    assert client.get("/api/session").json()["payments"] == "nessie"
    summary = client.get("/api/payments/card").json()
    assert summary["provider"] == "nessie"
    assert summary["settlement"]["credit_limit_cents"] == 100_000
    assert summary["card_count"] == 0


def test_nessie_mode_requires_key_and_card(monkeypatch):
    monkeypatch.setenv("PAYMENTS_MODE", "nessie")
    monkeypatch.delenv("NESSIE_API_KEY", raising=False)
    settings.cache_clear()
    with pytest.raises(RuntimeError, match="NESSIE_API_KEY"):
        settings().validate_runtime()
    settings.cache_clear()
