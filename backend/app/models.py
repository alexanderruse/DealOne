from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, DateTime, Float, JSON, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


def identifier():
    return str(uuid4())


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(180))
    variant: Mapped[str] = mapped_column(String(100))
    seller: Mapped[str] = mapped_column(String(100), default="DealOne Demo Store")
    condition: Mapped[str] = mapped_column(String(30), default="new")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    price_cents: Mapped[int] = mapped_column(Integer)
    original_cents: Mapped[int] = mapped_column(Integer)
    shipping_cents: Mapped[int] = mapped_column(Integer, default=0)
    tax_basis_points: Mapped[int] = mapped_column(Integer, default=625)
    stock: Mapped[int] = mapped_column(Integer, default=20)
    category: Mapped[str] = mapped_column(String(50))
    color: Mapped[str] = mapped_column(String(30), default="violet")
    # Seller/listing text is untrusted data. The trap product stores bait here on purpose.
    description: Mapped[str] = mapped_column(Text, default="")

    stock_status: Mapped[str] = mapped_column(String(20), default="unknown")
    source_kind: Mapped[str] = mapped_column(String(20), default="demo")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str] = mapped_column(String(30), default="package")
    price_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PriceObservation(Base):
    __tablename__ = "price_observations"
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, default=utcnow)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    product_id: Mapped[str] = mapped_column(String(64), index=True)
    price_cents: Mapped[int] = mapped_column(Integer)
    total_cents: Mapped[int] = mapped_column(Integer)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner: Mapped[str] = mapped_column(String(36), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    variant: Mapped[str] = mapped_column(String(100))
    seller: Mapped[str] = mapped_column(String(100))
    condition: Mapped[str] = mapped_column(String(30))
    currency: Mapped[str] = mapped_column(String(3))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    max_total_cents: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # ask_first (default): notify when the price matches; auto_buy: worker may purchase.
    checkout_mode: Mapped[str] = mapped_column(String(30), default="legacy")
    sandbox_total_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purchase_mode: Mapped[str] = mapped_column(String(20), default="ask_first")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VirtualCard(Base):
    """One single-use card per permission. Limit equals the approved all-in maximum."""
    __tablename__ = "virtual_cards"
    permission_id: Mapped[str] = mapped_column(ForeignKey("permissions.id"), primary_key=True)
    last4: Mapped[str] = mapped_column(String(4))
    limit_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="issued")  # issued | spent | voided
    nessie_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BankCharge(Base):
    """Durable DealOne ↔ Nessie transaction mapping for idempotency and the Activity ledger."""
    __tablename__ = "bank_charges"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    permission_id: Mapped[str] = mapped_column(ForeignKey("permissions.id"), index=True)
    account_id: Mapped[str] = mapped_column(String(64))
    transaction_id: Mapped[str] = mapped_column(String(80), unique=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|settled|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    permission_id: Mapped[str] = mapped_column(ForeignKey("permissions.id"), unique=True)
    owner: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    total_cents: Mapped[int] = mapped_column(Integer)
    confirmation: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MerchantOrder(Base):
    __tablename__ = "merchant_orders"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    confirmation: Mapped[str] = mapped_column(String(80), unique=True)
    total_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentAuthorization(Base):
    __tablename__ = "payment_authorizations"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    token: Mapped[str] = mapped_column(String(80), unique=True)
    merchant: Mapped[str] = mapped_column(String(100))
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="captured")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("owner", "event_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner: Mapped[str] = mapped_column(String(36), index=True)
    event_key: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Heartbeat(Base):
    __tablename__ = "heartbeats"
    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovedDomain(Base):
    __tablename__ = "approved_domains"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    domain: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AISpendRecord(Base):
    __tablename__ = "ai_spend_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    purpose: Mapped[str] = mapped_column(String(50))  # assistant_chat, rules_parse, checkout_demo
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="reserved", index=True)  # reserved, settled, failed
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    actual_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    generation_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class CheckoutDemoRun(Base):
    __tablename__ = "checkout_demo_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    permission_id: Mapped[str] = mapped_column(ForeignKey("permissions.id"), index=True)
    owner: Mapped[str] = mapped_column(String(36), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    trigger_type: Mapped[str] = mapped_column(String(30))  # manual_admin, price_drop
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)  # queued, running, completed, blocked, failed
    blocker_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    nessie_verified_limit_cents: Mapped[int] = mapped_column(Integer, default=0)
    fields_completed: Mapped[str] = mapped_column(Text, default="[]")  # JSON encoded list of completed fields
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    payment_screenshot_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    is_submitted: Mapped[bool] = mapped_column(Boolean, default=False)  # strictly False
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CatalogTask(Base):
    """Shared, deduplicated merchant requests. Contains public product data only."""
    __tablename__ = "catalog_tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    store: Mapped[str] = mapped_column(String(30), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    target: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    message: Mapped[str] = mapped_column(Text, default="Awaiting merchant verification")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
