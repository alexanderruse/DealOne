from datetime import timedelta
from unittest.mock import Mock
import pytest
from sqlalchemy import select
from app import payments
from app.models import Product, Permission, Order, BankCharge, VirtualCard, utcnow
from app.services import complete_order, PurchaseRejected
from tests.test_merchant_catalog import merchant_record


def add_real(db, status="in_stock"):
    record = merchant_record()
    record["stock_status"] = status
    record["stock"] = 1 if status == "in_stock" else 0
    db.add(Product(**record))
    db.commit()
    return record


def draft(client, product):
    return client.post('/api/permissions', json={"product_id":product["id"], "max_total_cents":6000,
        "expires_at":(utcnow()+timedelta(days=1)).isoformat()})


@pytest.mark.parametrize('status', ['sold_out','unknown'])
def test_unavailable_variant_cannot_be_requested(client, db, status):
    p = add_real(db, status)
    response = draft(client, p)
    assert response.status_code == 409
    assert list(db.scalars(select(Permission))) == []
    assert list(db.scalars(select(VirtualCard))) == []


def test_only_real_products_are_public(client, db):
    p = add_real(db)
    rows = client.get('/api/products').json()
    assert [row['id'] for row in rows] == [p['id']]
    assert rows[0]['can_request'] is True
    assert all(row['seller'] != 'DealOne Demo Store' for row in rows)


def test_sold_out_after_draft_cannot_activate(client, db):
    p = add_real(db)
    rule = draft(client,p).json()
    product=db.get(Product,p['id']); product.stock_status='sold_out'; product.stock=0; db.commit()
    response=client.post(f"/api/permissions/{rule['id']}/activate",json={'checkout_mode':'sandbox','sandbox_total_cents':5000})
    assert response.status_code == 409
    assert list(db.scalars(select(VirtualCard))) == []


def test_real_item_sandbox_receipt_never_claims_a_merchant_order(client, db):
    p=add_real(db)
    rule=draft(client,p).json()
    response=client.post(f"/api/permissions/{rule['id']}/activate",json={'checkout_mode':'sandbox','sandbox_total_cents':5000})
    assert response.status_code == 200
    assert response.json()['card']['limit_cents'] == 6000
    order=client.post('/api/purchases',json={'permission_id':rule['id']}).json()
    db.expire_all()
    result=complete_order(db,order['id']); db.commit()
    assert result.total_cents == 5000
    assert result.confirmation.startswith('SANDBOX-')
    receipt=client.get('/api/orders').json()[0]
    assert receipt['merchant_order_placed'] is False
    assert receipt['checkout_mode'] == 'sandbox'
    assert db.get(Product,p['id']).price_cents == 4999  # Scenario never rewrites a real price.
    assert db.get(Product,p['id']).stock == 1  # Nor real merchant inventory.


def test_stock_rechecked_before_bank_authorization(client,db,monkeypatch):
    p=add_real(db); rule=draft(client,p).json()
    client.post(f"/api/permissions/{rule['id']}/activate",json={'checkout_mode':'sandbox','sandbox_total_cents':5000})
    order=client.post('/api/purchases',json={'permission_id':rule['id']}).json()
    db.expire_all(); product=db.get(Product,p['id']); product.stock_status='sold_out'; product.stock=0; db.commit()
    bank=Mock(); monkeypatch.setattr(payments,'provider',bank)
    with pytest.raises(PurchaseRejected,match='sold out'): complete_order(db,order['id'])
    bank.authorize.assert_not_called()
    assert list(db.scalars(select(BankCharge))) == []


def test_sandbox_total_cannot_exceed_permission(client,db):
    p=add_real(db);rule=draft(client,p).json()
    response=client.post(f"/api/permissions/{rule['id']}/activate",json={'checkout_mode':'sandbox','sandbox_total_cents':6001})
    assert response.status_code == 409
    assert list(db.scalars(select(VirtualCard))) == []


def test_grok_cannot_bypass_ask_first_for_sandbox(client,db):
    from app.ai import execute_tool
    from app.schemas import PermissionReference
    p=add_real(db);rule=draft(client,p).json()
    client.post(f"/api/permissions/{rule['id']}/activate",json={'checkout_mode':'sandbox','sandbox_total_cents':5000})
    db.expire_all(); permission=db.get(Permission,rule['id'])
    with pytest.raises(PurchaseRejected,match='approval'):
        execute_tool('request_purchase',PermissionReference(permission_id=rule['id']),db,permission.owner)
    assert list(db.scalars(select(Order))) == []
