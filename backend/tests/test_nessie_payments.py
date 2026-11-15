"""Nessie provider tests. A fake transport stands in for the sandbox bank; no network is used."""
from datetime import timedelta
import json

import httpx
import pytest

from app import payments
from app.config import settings
from app.models import BankCharge, Order, PaymentAuthorization, Permission, Product, VirtualCard, utcnow
from app.payments import NessieProvider, PaymentUnavailable
from app.services import PurchaseRejected, complete_order, issue_card, prepare_order, settle_due_charges


def active(db, **changes):
    p = db.get(Product, "headphones")
    values = dict(owner="owner", product_id=p.id, variant=p.variant, seller=p.seller, condition=p.condition,
                  currency=p.currency, quantity=1, max_total_cents=25000,
                  expires_at=utcnow() + timedelta(days=1), status="active", purchase_mode="auto_buy")
    values.update(changes)
    rule = Permission(**values)
    db.add(rule)
    db.flush()
    issue_card(db, rule)
    db.commit()
    return rule


class FakeBank:
    """Nessie v2 stand-in: per-customer cards, purchases, settle/cancel, reset, and credit edits."""

    def __init__(self, limit_cents=100_000, fail_with=None):
        self.default_limit = limit_cents
        self.fail_with = fail_with
        self.accounts = {
            "acct_shared": {"id": "acct_shared", "type": "Credit Card", "nickname": "Shared",
                            "credit_limit_cents": limit_cents, "balance_cents": 0,
                            "available_cents": limit_cents, "pending_debits_cents": 0},
        }
        self.transactions = {}  # account_id -> [tx]
        self.merchants = [{"id": "mer_1", "name": "DealOne Demo Store"}]
        self.customers = [{"id": "cust_1", "first_name": "DealOne", "last_name": "Demo"}]
        self.requests = []
        self.account_seq = 1

    def _account_txs(self, account_id):
        return self.transactions.setdefault(account_id, [])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, request.headers.get("authorization")))
        assert "key=" not in str(request.url)
        if self.fail_with:
            raise self.fail_with
        path = request.url.path
        method = request.method
        if method == "GET" and path == "/merchants":
            return httpx.Response(200, json={"items": self.merchants})
        if method == "POST" and path == "/merchants":
            body = json.loads(request.content)
            merchant = {"id": f"mer_{len(self.merchants) + 1}", **body}
            self.merchants.append(merchant)
            return httpx.Response(201, json=merchant)
        if method == "GET" and path == "/customers":
            return httpx.Response(200, json={"items": self.customers})
        if method == "POST" and path == "/customers":
            body = json.loads(request.content)
            customer = {"id": f"cust_{len(self.customers) + 1}", **body}
            self.customers.append(customer)
            return httpx.Response(201, json=customer)
        if method == "POST" and path.startswith("/customers/") and path.endswith("/accounts"):
            body = json.loads(request.content)
            # Nessie v2 AccountCreateDTO rejects additional properties, including metadata.
            assert set(body) <= {"type", "nickname", "credit_limit_cents", "balance_cents", "rewards"}
            account_id = f"acct_{self.account_seq}"
            self.account_seq += 1
            limit = int(body.get("credit_limit_cents", self.default_limit))
            account = {"id": account_id, "type": body.get("type", "Credit Card"),
                       "nickname": body.get("nickname", "DealOne"), "credit_limit_cents": limit,
                       "balance_cents": 0, "available_cents": limit, "pending_debits_cents": 0}
            self.accounts[account_id] = account
            self.transactions[account_id] = []
            return httpx.Response(201, json=account)
        if method == "GET" and "/transactions" in path:
            account_id = path.split("/")[2]
            offset = int(request.url.params.get("offset", "0"))
            limit = int(request.url.params.get("limit", "100"))
            rows = self._account_txs(account_id)[offset:offset + limit]
            return httpx.Response(200, json={"items": rows})
        if method == "POST" and path.endswith("/purchases"):
            account_id = path.split("/")[2]
            body = json.loads(request.content)
            account = self.accounts[account_id]
            if body["amount_cents"] > account["available_cents"]:
                return httpx.Response(422, json={"type": "credit_limit_exceeded", "title": "Credit limit exceeded"})
            tx = {"id": f"txn_{account_id}_{len(self._account_txs(account_id)) + 1}", "kind": "purchase",
                  "status": "pending", "created_at": utcnow().isoformat(), **body}
            self._account_txs(account_id).append(tx)
            account["pending_debits_cents"] += body["amount_cents"]
            account["available_cents"] -= body["amount_cents"]
            return httpx.Response(201, json=tx)
        if method == "POST" and path.endswith("/settle"):
            tx_id = path.split("/")[2]
            for txs in self.transactions.values():
                for tx in txs:
                    if tx["id"] == tx_id:
                        tx["status"] = "completed"
                        return httpx.Response(200, json=tx)
            return httpx.Response(404, json={})
        if method == "POST" and path.endswith("/cancel"):
            tx_id = path.split("/")[2]
            for account_id, txs in self.transactions.items():
                for tx in txs:
                    if tx["id"] == tx_id:
                        if tx["status"] != "pending":
                            return httpx.Response(409, json={"type": "transaction_not_pending"})
                        tx["status"] = "cancelled"
                        account = self.accounts[account_id]
                        account["pending_debits_cents"] = max(0, account["pending_debits_cents"] - tx["amount_cents"])
                        account["available_cents"] += tx["amount_cents"]
                        return httpx.Response(200, json=tx)
            return httpx.Response(404, json={})
        if method == "POST" and path.endswith("/reset"):
            account_id = path.split("/")[2]
            for tx in self._account_txs(account_id):
                if tx["status"] == "pending":
                    tx["status"] = "cancelled"
            account = self.accounts[account_id]
            account["balance_cents"] = 0
            account["pending_debits_cents"] = 0
            account["available_cents"] = account["credit_limit_cents"]
            return httpx.Response(200, json=account)
        if method in ("PATCH", "PUT") and path.startswith("/accounts/"):
            account_id = path.split("/")[2]
            body = json.loads(request.content)
            account = self.accounts[account_id]
            account.update(body)
            return httpx.Response(200, json=account)
        if method == "GET" and path.startswith("/accounts/"):
            account_id = path.split("/")[2]
            return httpx.Response(200, json=self.accounts[account_id])
        return httpx.Response(404, json={})


@pytest.fixture
def nessie(monkeypatch):
    monkeypatch.setenv("PAYMENTS_MODE", "nessie")
    monkeypatch.setenv("NESSIE_API_KEY", "nsk_test")
    monkeypatch.setenv("NESSIE_CUSTOMER_ID", "cust_1")
    monkeypatch.setenv("NESSIE_MERCHANT_ID", "mer_1")
    monkeypatch.delenv("NESSIE_CARD_ACCOUNT_ID", raising=False)
    settings.cache_clear()
    bank = FakeBank()
    provider = NessieProvider()
    monkeypatch.setattr(provider, "_client", lambda: httpx.Client(
        base_url="https://bank.test", headers={"Authorization": "Bearer nsk_test"},
        transport=httpx.MockTransport(bank.handler)))
    monkeypatch.setattr(payments, "provider", provider)
    yield bank
    settings.cache_clear()


def test_activate_mints_a_nessie_card_per_permission(client, db, nessie):
    expires = (utcnow() + timedelta(days=1)).isoformat()
    created = client.post("/api/permissions", json={"product_id": "headphones", "max_total_cents": 18000,
                                                    "expires_at": expires}).json()
    activated = client.post(f"/api/permissions/{created['id']}/activate",
                            json={"purchase_mode": "auto_buy"}).json()
    assert activated["card"]["nessie_account_id"].startswith("acct_")
    assert activated["card"]["limit_cents"] == 18000
    assert nessie.accounts[activated["card"]["nessie_account_id"]]["credit_limit_cents"] == 18000


def test_purchase_is_charged_to_the_permission_card(db, nessie):
    permission = active(db)
    card = db.get(VirtualCard, permission.id)
    order = prepare_order(db, permission.id)
    completed = complete_order(db, order.id)
    db.commit()
    assert completed.status == "completed"
    authorization = db.get(PaymentAuthorization, order.id)
    charge = db.get(BankCharge, order.id)
    assert authorization.token == charge.transaction_id
    assert charge.account_id == card.nessie_account_id
    assert charge.status == "pending"
    assert nessie.transactions[card.nessie_account_id][0]["amount_cents"] == order.total_cents
    assert all(auth == "Bearer nsk_test" for _, _, auth in nessie.requests)


def test_card_limit_decline_marks_watch_declined(db, nessie):
    permission = active(db)
    card = db.get(VirtualCard, permission.id)
    nessie.accounts[card.nessie_account_id]["available_cents"] = 1_000
    order = prepare_order(db, permission.id)
    db.commit()
    with pytest.raises(PurchaseRejected, match="card limit"):
        complete_order(db, order.id)
    db.commit()
    db.expire_all()
    assert db.get(Order, order.id).status == "declined"
    assert db.get(Permission, permission.id).status == "declined"
    assert db.get(PaymentAuthorization, order.id) is None
    assert nessie.transactions[card.nessie_account_id] == []


def test_retry_reuses_mapped_bank_charge(db, nessie):
    permission = active(db)
    order = prepare_order(db, permission.id)
    db.commit()
    complete_order(db, order.id)
    db.rollback()
    card = db.get(VirtualCard, permission.id)
    assert len(nessie.transactions[card.nessie_account_id]) == 1
    # Rematerialize after rollback using the durable mapping + bank lookup.
    complete_order(db, order.id)
    db.commit()
    assert len(nessie.transactions[card.nessie_account_id]) == 1
    assert db.get(BankCharge, order.id).transaction_id.endswith("_1")


def test_settle_after_cancel_window(db, nessie):
    permission = active(db)
    order = prepare_order(db, permission.id)
    complete_order(db, order.id)
    db.commit()
    order = db.get(Order, order.id)
    order.updated_at = utcnow() - timedelta(seconds=settings().cancel_window_seconds + 1)
    db.commit()
    assert settle_due_charges(db) == [order.id]
    db.commit()
    assert db.get(PaymentAuthorization, order.id).status == "settled"
    assert db.get(BankCharge, order.id).status == "settled"
    card = db.get(VirtualCard, permission.id)
    assert nessie.transactions[card.nessie_account_id][0]["status"] == "completed"


def test_unreachable_bank_fails_closed(db, nessie):
    permission = active(db)
    order = prepare_order(db, permission.id)
    db.commit()
    nessie.fail_with = httpx.ConnectError("down")
    with pytest.raises(PaymentUnavailable):
        complete_order(db, order.id)
    db.rollback()
    assert db.get(PaymentAuthorization, order.id) is None
    assert db.get(Permission, permission.id).status == "active"


def test_storefront_route_maps_bank_answers_to_http(client, db, nessie):
    headers = {"x-worker-token": settings().worker_token}
    expires = (utcnow() + timedelta(days=1)).isoformat()
    created = client.post("/api/permissions", json={"product_id": "headphones", "max_total_cents": 25000,
                                                    "expires_at": expires}).json()
    client.post(f"/api/permissions/{created['id']}/activate", json={"purchase_mode": "auto_buy"})
    order = client.post("/api/purchases", json={"permission_id": created["id"]}).json()
    nessie.fail_with = httpx.ConnectError("down")
    assert client.post(f"/api/storefront/orders/{order['id']}", headers=headers).status_code == 503
    nessie.fail_with = None
    card = db.get(VirtualCard, created["id"])
    nessie.accounts[card.nessie_account_id]["available_cents"] = 1_000
    declined = client.post(f"/api/storefront/orders/{order['id']}", headers=headers)
    assert declined.status_code == 409 and "card limit" in declined.json()["detail"]
    assert client.get("/api/orders").json()[0]["status"] == "declined"


def test_ledger_seed_reset_and_credit_controls(client, db, nessie):
    assert client.get("/api/session").json()["payments"] == "nessie"
    seeded = client.post("/api/demo/nessie/seed", headers={"x-demo-admin-token": settings().demo_admin_token})
    assert seeded.status_code == 200
    assert seeded.json()["merchant_id"] == "mer_1"
    expires = (utcnow() + timedelta(days=1)).isoformat()
    created = client.post("/api/permissions", json={"product_id": "headphones", "max_total_cents": 25000,
                                                    "expires_at": expires}).json()
    activated = client.post(f"/api/permissions/{created['id']}/activate",
                            json={"purchase_mode": "auto_buy"}).json()
    order = client.post("/api/purchases", json={"permission_id": created["id"]}).json()
    headers = {"x-worker-token": settings().worker_token}
    assert client.post(f"/api/storefront/orders/{order['id']}", headers=headers).json()["status"] == "completed"
    ledger = client.get("/api/payments/ledger").json()
    assert ledger["charges"][0]["transaction_id"] == db.get(BankCharge, order["id"]).transaction_id
    assert ledger["nessie"][0]["account_id"] == activated["card"]["nessie_account_id"]
    credit = client.post("/api/demo/nessie/credit",
                         headers={"x-demo-admin-token": settings().demo_admin_token},
                         json={"permission_id": created["id"], "available_cents": 500})
    assert credit.status_code == 200
    assert credit.json()["available_cents"] == 500
    reset = client.post("/api/demo/nessie/reset",
                        headers={"x-demo-admin-token": settings().demo_admin_token},
                        json={"permission_id": created["id"], "available_cents": 0})
    assert reset.status_code == 200
    account_id = activated["card"]["nessie_account_id"]
    assert nessie.accounts[account_id]["available_cents"] == nessie.accounts[account_id]["credit_limit_cents"]


def test_nessie_mode_requires_key_and_customer_or_card(monkeypatch):
    monkeypatch.setenv("PAYMENTS_MODE", "nessie")
    monkeypatch.setenv("NESSIE_API_KEY", "")
    monkeypatch.setenv("NESSIE_CUSTOMER_ID", "")
    monkeypatch.setenv("NESSIE_CARD_ACCOUNT_ID", "")
    settings.cache_clear()
    with pytest.raises(RuntimeError, match="NESSIE_API_KEY"):
        settings().validate_runtime()
    settings.cache_clear()
