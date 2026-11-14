from datetime import timedelta
from .config import settings
from sqlalchemy import select
from .models import (Product, Permission, Order, MerchantOrder, PaymentAuthorization,
                     PriceObservation, Notification, utcnow)
from . import payments
from .payments import PaymentDeclined


class PurchaseRejected(ValueError):
    pass


def total(product):
    # USD integer cents; round tax half up, never use binary floating point.
    tax = (product.price_cents * product.tax_basis_points + 5000) // 10000
    return product.price_cents + product.shipping_cents + tax


def serialize(record):
    return {c.name: getattr(record, c.name) for c in record.__table__.columns}


def product_view(product):
    return {**serialize(product), "total_cents": total(product)}


def validate_purchase(permission, product):
    if permission.status != "active":
        raise PurchaseRejected("Purchase permission is not active")
    if permission.expires_at <= utcnow():
        raise PurchaseRejected("Purchase permission has expired")
    if permission.quantity != 1:
        raise PurchaseRejected("Only one item per permission is supported")
    for field in ("variant", "seller", "condition", "currency"):
        if getattr(permission, field) != getattr(product, field):
            raise PurchaseRejected(f"Product {field} no longer matches your permission")
    if product.stock < 1:
        raise PurchaseRejected("Product is out of stock")
    if total(product) > permission.max_total_cents:
        raise PurchaseRejected("Final total including tax and shipping exceeds your budget")


def price_baseline(db, product_id):
    """Median all-in price over the last 30 days, ignoring the most recent hour.

    The seller's claimed discount is never used: a deal is judged against prices we observed.
    """
    now = utcnow()
    totals = sorted(db.scalars(select(PriceObservation.total_cents).where(
        PriceObservation.product_id == product_id,
        PriceObservation.observed_at >= now - timedelta(days=30),
        PriceObservation.observed_at < now - timedelta(hours=1))))
    return totals[len(totals) // 2] if len(totals) >= 5 else None


def too_good_to_be_true(db, product):
    baseline = price_baseline(db, product.id)
    return baseline is not None and total(product) * 2 < baseline


def reviewed_key(permission_id):
    return f"reviewed:{permission_id}"


def notify(db, owner, key, message):
    if not db.scalar(select(Notification).where(Notification.owner == owner, Notification.event_key == key)):
        db.add(Notification(owner=owner, event_key=key, message=message))


def prepare_order(db, permission_id, owner=None):
    permission = db.scalar(select(Permission).where(Permission.id == permission_id).with_for_update())
    if not permission or (owner is not None and permission.owner != owner):
        raise PurchaseRejected("Permission not found")
    existing = db.scalar(select(Order).where(Order.permission_id == permission.id))
    if existing:
        return existing
    product = db.get(Product, permission.product_id)
    validate_purchase(permission, product)
    reviewed = db.scalar(select(Notification).where(Notification.owner == permission.owner,
                                                    Notification.event_key == reviewed_key(permission.id)))
    if not reviewed and too_good_to_be_true(db, product):
        # Applies to every caller, including Grok's tool. Only the user can clear it.
        permission.status = "paused"
        notify(db, permission.owner, f"suspicious:{permission.id}",
               f"Paused for your review: {product.name} is ${total(product)/100:.2f}, less than half its usual "
               "price. Deals this far below the tracked price can be scams.")
        return None
    order = Order(permission_id=permission.id, owner=permission.owner, total_cents=total(product))
    db.add(order)
    db.flush()
    notify(db, permission.owner, f"drop:{permission.id}",
           f"Price target reached: {product.name} is ${total(product)/100:.2f} all in. Preparing your purchase.")
    return order


def complete_order(db, order_id):
    # Permission -> order -> product is the consistent lock order everywhere.
    reference = db.get(Order, order_id)
    if not reference:
        raise PurchaseRejected("Order not found")
    permission = db.scalar(select(Permission).where(Permission.id == reference.permission_id).with_for_update())
    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    existing = db.get(MerchantOrder, order.id)
    if existing:
        return reconcile_order(db, order, existing)
    if order.status not in ("pending", "submitting", "uncertain"):
        raise PurchaseRejected("Order is not eligible for submission")
    product = db.scalar(select(Product).where(Product.id == permission.product_id).with_for_update())
    validate_purchase(permission, product)
    # A changed checkout total requires a fresh quote instead of silently changing the order.
    if total(product) != order.total_cents:
        raise PurchaseRejected("Checkout total changed; review and create a new permission")
    try:
        payments.provider.authorize(db, order_id=order.id, merchant=product.seller, amount_cents=total(product))
    except PaymentDeclined as exc:
        raise PurchaseRejected(f"Card declined: {exc}") from None
    merchant = MerchantOrder(order_id=order.id, confirmation="DO-" + order.id[:8].upper(), total_cents=total(product))
    db.add(merchant)
    product.stock -= 1
    db.flush()
    return reconcile_order(db, order, merchant)


def cancel_deadline(order):
    return order.updated_at + timedelta(seconds=settings().cancel_window_seconds)


def cancel_order(db, order_id, owner):
    """Void a just-completed purchase while the bank still holds it as pending."""
    reference = db.get(Order, order_id)
    if not reference or reference.owner != owner:
        raise PurchaseRejected("Order not found")
    permission = db.scalar(select(Permission).where(Permission.id == reference.permission_id).with_for_update())
    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order.status != "completed":
        raise PurchaseRejected("Only a completed purchase can be cancelled")
    if cancel_deadline(order) <= utcnow():
        raise PurchaseRejected("The cancellation window has closed")
    product = db.scalar(select(Product).where(Product.id == permission.product_id).with_for_update())
    authorization = db.get(PaymentAuthorization, order.id)
    try:
        payments.provider.void(authorization.token)
    except PaymentDeclined as exc:
        raise PurchaseRejected(f"Too late to cancel: {exc}") from None
    authorization.status = "voided"
    merchant = db.get(MerchantOrder, order.id)
    if merchant:
        db.delete(merchant)
    product.stock += 1
    order.status = "cancelled"
    order.updated_at = utcnow()
    permission.status = "cancelled"
    notify(db, order.owner, f"void:{order.id}",
           f"Purchase cancelled within the window. ${order.total_cents/100:.2f} was released; nothing was charged.")
    return order


def resume_after_review(db, permission_id, owner):
    """The user looked at a flagged price and chose to continue. Recorded as an audit entry."""
    permission = db.scalar(select(Permission).where(Permission.id == permission_id,
                                                    Permission.owner == owner).with_for_update())
    if not permission:
        raise PurchaseRejected("Permission not found")
    flagged = db.scalar(select(Notification).where(Notification.owner == owner,
                                                   Notification.event_key == f"suspicious:{permission.id}"))
    if permission.status != "paused" or not flagged:
        raise PurchaseRejected("Only a watch paused for price review can be resumed")
    if permission.expires_at <= utcnow():
        raise PurchaseRejected("Purchase permission has expired")
    permission.status = "active"
    notify(db, owner, reviewed_key(permission.id), "You reviewed the unusually low price and chose to continue.")
    return permission


def reconcile_order(db, order, merchant):
    order.status = "completed"
    order.confirmation = merchant.confirmation
    order.error = None
    order.updated_at = utcnow()
    permission = db.get(Permission, order.permission_id)
    permission.status = "purchased"
    authorization = db.get(PaymentAuthorization, order.id)
    paid_with = "Nessie sandbox card" if authorization and not authorization.token.startswith("sim_") else "simulated card"
    notify(db, order.owner, f"receipt:{order.id}",
           f"Purchase complete on {paid_with}. Receipt {merchant.confirmation} · ${merchant.total_cents/100:.2f}.")
    return order


def seed(db):
    if db.get(Product, "headphones"):
        return
    products = [
        Product(id="headphones", name="Studio Wireless Headphones", variant="Midnight / New", category="Audio", color="violet", price_cents=19900, original_cents=24900),
        Product(id="keyboard", name="Mechanical Keyboard", variant="Ivory / US layout / New", category="Workspace", color="sand", price_cents=12900, original_cents=15900),
        Product(id="speaker", name="Portable Bluetooth Speaker", variant="Sage / New", category="Audio", color="sage", price_cents=8900, original_cents=11900),
    ]
    db.add_all(products)
    db.flush()
    for product in products:
        for day in range(14, -1, -1):
            historical = product.price_cents + (day // 3) * 300
            db.add(PriceObservation(product_id=product.id, observed_at=utcnow()-timedelta(days=day),
                                    price_cents=historical,
                                    total_cents=historical + (historical*625+5000)//10000))
