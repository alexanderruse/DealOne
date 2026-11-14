from datetime import timedelta
from sqlalchemy import select
from .models import (Product, Permission, Order, MerchantOrder, PaymentAuthorization,
                     PriceObservation, Notification, utcnow)
from .payments import provider


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
    provider.authorize(db, order_id=order.id, merchant=product.seller, amount_cents=total(product))
    merchant = MerchantOrder(order_id=order.id, confirmation="DO-" + order.id[:8].upper(), total_cents=total(product))
    db.add(merchant)
    product.stock -= 1
    db.flush()
    return reconcile_order(db, order, merchant)


def reconcile_order(db, order, merchant):
    order.status = "completed"
    order.confirmation = merchant.confirmation
    order.error = None
    order.updated_at = utcnow()
    permission = db.get(Permission, order.permission_id)
    permission.status = "purchased"
    notify(db, order.owner, f"receipt:{order.id}",
           f"Simulated purchase complete. Receipt {merchant.confirmation} · ${merchant.total_cents/100:.2f}.")
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
