"""Payment boundary.

Two providers: the default simulation, and Capital One's Nessie v2 sandbox bank.
Both are transactional per order and single-use. Neither moves real money.
"""
from typing import Protocol
import secrets

import httpx

from .config import settings
from .models import PaymentAuthorization


class PaymentDeclined(ValueError):
    """The bank refused the charge, for example because it exceeds the card limit."""


class PaymentUnavailable(RuntimeError):
    """The bank could not be reached or answered unexpectedly. Nothing is assumed paid."""


class PaymentProvider(Protocol):
    def authorize(self, db, *, order_id: str, merchant: str, amount_cents: int) -> PaymentAuthorization: ...


class SimulatedVirtualCardProvider:
    def authorize(self, db, *, order_id: str, merchant: str, amount_cents: int) -> PaymentAuthorization:
        if amount_cents <= 0:
            raise ValueError("Authorization amount must be positive")
        # The primary key is the purchase idempotency key. An authorization cannot be reused.
        authorization = PaymentAuthorization(order_id=order_id, token="sim_" + secrets.token_hex(16),
                                              merchant=merchant, amount_cents=amount_cents, status="captured")
        db.add(authorization)
        db.flush()
        return authorization


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


class NessieProvider:
    """Charges a Nessie sandbox card. Nessie enforces the card limit itself.

    The API key travels only in the Authorization header. Grok never sees it.
    """

    def _client(self) -> httpx.Client:
        config = settings()
        return httpx.Client(base_url=config.nessie_base_url.rstrip("/"),
                            headers={"Authorization": f"Bearer {config.nessie_api_key}"}, timeout=10.0)

    @staticmethod
    def _description(order_id: str) -> str:
        return f"DEALONE {order_id}"

    def _find_existing(self, client, account_id: str, order_id: str):
        # Nessie has no idempotency key, so look for this order's charge before creating one.
        response = client.get(f"/accounts/{account_id}/transactions", params={"limit": 100})
        response.raise_for_status()
        for tx in _items(response.json()):
            if tx.get("description") == self._description(order_id) and tx.get("status") in ("pending", "completed"):
                return tx
        return None

    def _merchant_id(self, client) -> str:
        configured = settings().nessie_merchant_id
        if configured:
            return configured
        response = client.get("/merchants", params={"limit": 100})
        response.raise_for_status()
        merchants = _items(response.json())
        if not merchants:
            raise PaymentUnavailable("No Nessie merchant exists. Run POST /data/seed or set NESSIE_MERCHANT_ID")
        return str(merchants[0]["id"])

    def authorize(self, db, *, order_id: str, merchant: str, amount_cents: int) -> PaymentAuthorization:
        if amount_cents <= 0:
            raise ValueError("Authorization amount must be positive")
        account_id = settings().nessie_card_account_id
        try:
            with self._client() as client:
                tx = self._find_existing(client, account_id, order_id)
                if tx is None:
                    response = client.post(f"/accounts/{account_id}/purchases", json={
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
                    if settings().nessie_settle_immediately:
                        # Skips the pending window for the demo. Best effort: it settles on its own anyway.
                        try:
                            client.post(f"/transactions/{tx['id']}/settle")
                        except httpx.HTTPError:
                            pass
        except httpx.HTTPStatusError as exc:
            raise PaymentUnavailable(f"Nessie returned HTTP {exc.response.status_code}") from None
        except httpx.HTTPError as exc:
            raise PaymentUnavailable(f"Nessie request failed: {type(exc).__name__}") from None
        authorization = PaymentAuthorization(order_id=order_id, token=str(tx["id"]), merchant=merchant,
                                              amount_cents=amount_cents, status="captured")
        db.add(authorization)
        db.flush()
        return authorization

    def card(self) -> dict:
        """Current state of the sandbox card, for display."""
        try:
            with self._client() as client:
                response = client.get(f"/accounts/{settings().nessie_card_account_id}")
                response.raise_for_status()
                account = response.json()
        except httpx.HTTPError as exc:
            raise PaymentUnavailable(f"Nessie request failed: {type(exc).__name__}") from None
        return {key: account.get(key) for key in
                ("type", "nickname", "credit_limit_cents", "balance_cents", "pending_debits_cents", "available_cents")}


def _select_provider() -> PaymentProvider:
    return NessieProvider() if settings().payments_mode == "nessie" else SimulatedVirtualCardProvider()


provider: PaymentProvider = _select_provider()
