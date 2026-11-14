"""Payment boundary: the only shipped provider is simulated, transactional, and single-use."""
from typing import Protocol
import secrets
from .models import PaymentAuthorization


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


provider: PaymentProvider = SimulatedVirtualCardProvider()
