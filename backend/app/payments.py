"""Payment boundary.

Two providers: the default simulation, and Capital One's Nessie v2 sandbox bank.
Both are transactional per order and single-use. Neither moves real money.

In Nessie mode each DealOne permission preferably gets its own sandbox credit card
capped at the approved all-in maximum. A shared fallback account is still supported.
"""
from typing import Protocol
import secrets

import httpx

from .config import settings
from .models import BankCharge, PaymentAuthorization, utcnow


class PaymentDeclined(ValueError):
    """The bank refused the charge, for example because it exceeds the card limit."""


class PaymentUnavailable(RuntimeError):
    """The bank could not be reached or answered unexpectedly. Nothing is assumed paid."""


class PaymentProvider(Protocol):
    def create_card(self, *, permission_id: str, limit_cents: int, last4: str) -> str | None: ...

    def authorize(self, db, *, order_id: str, permission_id: str, account_id: str | None,
                  merchant: str, amount_cents: int) -> PaymentAuthorization: ...

    def void(self, token: str) -> None: ...

    def settle(self, token: str) -> None: ...

    def card(self, account_id: str | None = None) -> dict: ...

    def ledger(self, account_ids: list[str]) -> list[dict]: ...

    def seed(self) -> dict: ...

    def reset_account(self, account_id: str) -> dict: ...

    def set_available_credit(self, account_id: str, available_cents: int) -> dict: ...


def _items(payload):
    """Nessie v2 lists are paged objects ({"items": [...]}); accept a bare list too."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    return []


_DECLINE_REASONS = {
    "credit_limit_exceeded": "the card limit would be exceeded",
    "insufficient_funds": "the account has insufficient funds",
}


class SimulatedVirtualCardProvider:
    def create_card(self, *, permission_id: str, limit_cents: int, last4: str) -> str | None:
        return None

    def authorize(self, db, *, order_id: str, permission_id: str, account_id: str | None,
                  merchant: str, amount_cents: int) -> PaymentAuthorization:
        if amount_cents <= 0:
            raise ValueError("Authorization amount must be positive")
        existing = db.get(BankCharge, order_id)
        if existing:
            authorization = db.get(PaymentAuthorization, order_id)
            if authorization:
                return authorization
        token = "sim_" + secrets.token_hex(16)
        authorization = PaymentAuthorization(order_id=order_id, token=token, merchant=merchant,
                                              amount_cents=amount_cents, status="captured")
        db.add(authorization)
        db.add(BankCharge(order_id=order_id, permission_id=permission_id, account_id=account_id or "sim",
                          transaction_id=token, amount_cents=amount_cents, status="settled"))
        db.flush()
        return authorization

    def void(self, token: str) -> None:
        return None

    def settle(self, token: str) -> None:
        return None

    def card(self, account_id: str | None = None) -> dict:
        return {"provider": "simulation", "account_id": account_id}

    def ledger(self, account_ids: list[str]) -> list[dict]:
        return []

    def seed(self) -> dict:
        return {"provider": "simulation", "merchant_id": None, "customer_id": None}

    def reset_account(self, account_id: str) -> dict:
        return {"provider": "simulation", "account_id": account_id, "reset": False}

    def set_available_credit(self, account_id: str, available_cents: int) -> dict:
        return {"provider": "simulation", "account_id": account_id, "available_cents": available_cents}


class NessieProvider:
    """Charges Nessie sandbox cards. Prefer one card per permission.

    The API key travels only in the Authorization header. Grok never sees it.
    """

    def _client(self) -> httpx.Client:
        config = settings()
        return httpx.Client(base_url=config.nessie_base_url.rstrip("/"),
                            headers={"Authorization": f"Bearer {config.nessie_api_key}"}, timeout=10.0)

    @staticmethod
    def _description(order_id: str) -> str:
        return f"DEALONE {order_id}"

    def _raise_unavailable(self, exc: Exception):
        if isinstance(exc, httpx.HTTPStatusError):
            raise PaymentUnavailable(f"Nessie returned HTTP {exc.response.status_code}") from None
        raise PaymentUnavailable(f"Nessie request failed: {type(exc).__name__}") from None

    def _merchant_id(self, client) -> str:
        configured = settings().nessie_merchant_id
        if configured:
            return configured
        response = client.get("/merchants", params={"limit": 100})
        response.raise_for_status()
        merchants = _items(response.json())
        if not merchants:
            raise PaymentUnavailable("No Nessie merchant exists. Run demo Nessie seed or set NESSIE_MERCHANT_ID")
        return str(merchants[0]["id"])

    def _page_transactions(self, client, account_id: str):
        """Page through sandbox transactions so idempotency is not limited to the first page."""
        response = client.get(f"/accounts/{account_id}/transactions", params={"limit": 5000})
        response.raise_for_status()
        batch = _items(response.json())
        yield from batch
        if len(batch) >= 5000:
            raise PaymentUnavailable("Transaction history reached the provider limit; reconcile before charging again")

    def _find_existing(self, client, account_id: str, order_id: str):
        needle = self._description(order_id)
        for tx in self._page_transactions(client, account_id):
            if tx.get("description") == needle and tx.get("status") in ("pending", "completed"):
                return tx
        return None

    def create_card(self, *, permission_id: str, limit_cents: int, last4: str) -> str | None:
        customer_id = settings().nessie_customer_id
        if not customer_id:
            # Shared fallback account — bank limit is not per-permission in this mode.
            return settings().nessie_card_account_id or None
        try:
            with self._client() as client:
                response = client.post(f"/customers/{customer_id}/accounts", json={
                    "type": "Credit Card",
                    "nickname": f"DealOne {permission_id[:8]} {last4}",
                    "credit_limit_cents": limit_cents,
                    "balance_cents": 0,
                })
                response.raise_for_status()
                account = response.json()
                return str(account.get("id") or account.get("account_id"))
        except httpx.HTTPError as exc:
            self._raise_unavailable(exc)

    def authorize(self, db, *, order_id: str, permission_id: str, account_id: str | None,
                  merchant: str, amount_cents: int) -> PaymentAuthorization:
        if amount_cents <= 0:
            raise ValueError("Authorization amount must be positive")
        mapped = db.get(BankCharge, order_id)
        if mapped:
            existing_auth = db.get(PaymentAuthorization, order_id)
            if existing_auth:
                return existing_auth
            authorization = PaymentAuthorization(order_id=order_id, token=mapped.transaction_id,
                                                  merchant=merchant, amount_cents=amount_cents,
                                                  status="pending" if mapped.status == "pending" else "settled")
            db.add(authorization)
            db.flush()
            return authorization

        account = account_id or settings().nessie_card_account_id
        if not account:
            raise PaymentUnavailable("No Nessie card account is linked to this permission")
        try:
            with self._client() as client:
                tx = self._find_existing(client, account, order_id)
                if tx is None:
                    response = client.post(f"/accounts/{account}/purchases", json={
                        "amount_cents": amount_cents,
                        "merchant_id": self._merchant_id(client),
                        "description": self._description(order_id),
                    })
                    if response.status_code == 422:
                        problem = response.json() if response.content else {}
                        kind = str(problem.get("type", ""))
                        reason = next((text for key, text in _DECLINE_REASONS.items() if key in kind),
                                      str(problem.get("title") or problem.get("detail") or "the bank declined the charge"))
                        raise PaymentDeclined(reason)
                    response.raise_for_status()
                    tx = response.json()
                    if settings().nessie_settle_immediately and settings().cancel_window_seconds <= 0:
                        try:
                            client.post(f"/transactions/{tx['id']}/settle")
                            tx["status"] = "completed"
                        except httpx.HTTPError:
                            pass
        except PaymentDeclined:
            raise
        except httpx.HTTPError as exc:
            self._raise_unavailable(exc)

        token = str(tx["id"])
        bank_status = "settled" if tx.get("status") == "completed" else "pending"
        # Persist the DealOne↔Nessie mapping before depending on later commits.
        db.add(BankCharge(order_id=order_id, permission_id=permission_id, account_id=account,
                          transaction_id=token, amount_cents=amount_cents, status=bank_status,
                          created_at=utcnow()))
        authorization = PaymentAuthorization(order_id=order_id, token=token, merchant=merchant,
                                              amount_cents=amount_cents,
                                              status="settled" if bank_status == "settled" else "pending")
        db.add(authorization)
        db.flush()
        return authorization

    def void(self, token: str) -> None:
        """Cancel a still-pending charge. Nessie releases the hold on the card."""
        try:
            with self._client() as client:
                response = client.post(f"/transactions/{token}/cancel")
                if response.status_code in (409, 422):
                    raise PaymentDeclined("the charge has already settled")
                response.raise_for_status()
        except PaymentDeclined:
            raise
        except httpx.HTTPError as exc:
            self._raise_unavailable(exc)

    def settle(self, token: str) -> None:
        """Capture a pending charge after the cancel window closes."""
        try:
            with self._client() as client:
                response = client.post(f"/transactions/{token}/settle")
                if response.status_code in (409, 422):
                    # Already settled is fine for an idempotent worker tick.
                    return
                response.raise_for_status()
        except httpx.HTTPError as exc:
            self._raise_unavailable(exc)

    def card(self, account_id: str | None = None) -> dict:
        account = account_id or settings().nessie_card_account_id
        if not account:
            return {"provider": "nessie", "account_id": None}
        try:
            with self._client() as client:
                response = client.get(f"/accounts/{account}")
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            self._raise_unavailable(exc)
        return {"provider": "nessie", "account_id": account,
                **{key: payload.get(key) for key in
                   ("type", "nickname", "credit_limit_cents", "balance_cents",
                    "pending_debits_cents", "available_cents")}}

    def ledger(self, account_ids: list[str]) -> list[dict]:
        rows = []
        try:
            with self._client() as client:
                for account_id in account_ids:
                    for tx in self._page_transactions(client, account_id):
                        rows.append({
                            "account_id": account_id,
                            "transaction_id": str(tx.get("id")),
                            "status": tx.get("status"),
                            "amount_cents": tx.get("amount_cents"),
                            "description": tx.get("description"),
                            "merchant_id": tx.get("merchant_id"),
                            "created_at": tx.get("created_at") or tx.get("purchase_date"),
                        })
        except httpx.HTTPError as exc:
            self._raise_unavailable(exc)
        rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
        return rows

    def seed(self) -> dict:
        """Ensure a DealOne merchant (and optional customer) exist in the sandbox."""
        config = settings()
        try:
            with self._client() as client:
                merchant_id = config.nessie_merchant_id
                if not merchant_id:
                    listed = client.get("/merchants", params={"limit": 100})
                    listed.raise_for_status()
                    merchants = _items(listed.json())
                    dealone = next((m for m in merchants if str(m.get("name", "")).startswith("DealOne")), None)
                    if dealone:
                        merchant_id = str(dealone["id"])
                    else:
                        created = client.post("/merchants", json={
                            "name": "DealOne Sandbox Checkout",
                            "category": "electronics",
                        })
                        created.raise_for_status()
                        merchant_id = str(created.json()["id"])
                customer_id = config.nessie_customer_id
                if not customer_id:
                    customers = client.get("/customers", params={"limit": 20})
                    customers.raise_for_status()
                    items = _items(customers.json())
                    if items:
                        customer_id = str(items[0]["id"])
                    else:
                        created = client.post("/customers", json={
                            "first_name": "DealOne",
                            "last_name": "Demo",
                            "address": {"street_number": "1680", "street_name": "Capital One Dr",
                                        "city": "McLean", "state": "VA", "zip": "22102"},
                        })
                        created.raise_for_status()
                        customer_id = str(created.json()["id"])
        except httpx.HTTPError as exc:
            self._raise_unavailable(exc)
        return {"provider": "nessie", "merchant_id": merchant_id, "customer_id": customer_id}

    def reset_account(self, account_id: str) -> dict:
        """Cancel pending charges and clear demo holds so the card can be reused in a walkthrough."""
        cancelled = 0
        try:
            with self._client() as client:
                for tx in list(self._page_transactions(client, account_id)):
                    if tx.get("status") != "pending":
                        continue
                    response = client.post(f"/transactions/{tx['id']}/cancel")
                    if response.status_code < 300:
                        cancelled += 1
                # Optional sandbox reset hook used by v2 demo environments.
                response = client.post(f"/accounts/{account_id}/reset")
                if response.status_code not in (404, 405):
                    response.raise_for_status()
        except httpx.HTTPError as exc:
            self._raise_unavailable(exc)
        return {"provider": "nessie", "account_id": account_id, "cancelled_pending": cancelled, "reset": True}

    def set_available_credit(self, account_id: str, available_cents: int) -> dict:
        """Force a low available balance so a walkthrough can demonstrate a bank decline."""
        if available_cents < 0:
            raise ValueError("available_cents must be non-negative")
        try:
            with self._client() as client:
                current = client.get(f"/accounts/{account_id}")
                current.raise_for_status()
                account = current.json()
                limit = int(account.get("credit_limit_cents") or available_cents)
                balance = max(0, limit - available_cents)
                response = client.patch(f"/accounts/{account_id}", json={
                    "credit_limit_cents": limit,
                    "balance_cents": balance,
                    "available_cents": available_cents,
                })
                if response.status_code == 404:
                    response = client.put(f"/accounts/{account_id}", json={
                        "credit_limit_cents": limit,
                        "balance_cents": balance,
                        "available_cents": available_cents,
                    })
                response.raise_for_status()
                payload = response.json() if response.content else account
        except httpx.HTTPError as exc:
            self._raise_unavailable(exc)
        return {"provider": "nessie", "account_id": account_id,
                "credit_limit_cents": payload.get("credit_limit_cents", limit),
                "available_cents": payload.get("available_cents", available_cents),
                "balance_cents": payload.get("balance_cents", balance)}


def _select_provider() -> PaymentProvider:
    return NessieProvider() if settings().payments_mode == "nessie" else SimulatedVirtualCardProvider()


provider: PaymentProvider = _select_provider()
