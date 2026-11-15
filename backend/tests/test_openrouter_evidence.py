from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.ai import check_and_reserve_budget, fail_budget_spend, get_ai_budget_summary, settle_budget_spend
from app.config import settings
from app.models import AISpendRecord, ApprovedDomain, CheckoutDemoRun, Permission, Product, VirtualCard, utcnow
from app.worker import is_domain_approved, run_checkout_demo


def test_ai_budget_enforcement_and_rolling_recovery(db):
    # Reserve initial budget
    record1 = check_and_reserve_budget(db, purpose="test_initial", estimated_cost=1.50)
    assert record1.status == "reserved"

    # Settle with actual cost
    fake_resp = Mock()
    fake_resp.usage.to_dict.return_value = {"cost_details": {"upstream_inference_cost": 2.50}}
    settle_budget_spend(db, record1.id, fake_resp)
    assert record1.status == "settled"
    assert record1.actual_cost_usd == 2.50

    # Add spend to approach ceiling ($10 limit)
    record2 = AISpendRecord(
        purpose="fill_budget",
        model=settings().openrouter_model,
        status="settled",
        estimated_cost_usd=7.00,
        actual_cost_usd=7.00,
        created_at=utcnow()
    )
    db.add(record2)
    db.commit()

    # Total spend is now 2.50 + 7.00 = 9.50. Attempting to reserve 1.00 should exceed 10.00 and raise 429
    with pytest.raises(HTTPException) as exc:
        check_and_reserve_budget(db, purpose="exceed_budget", estimated_cost=1.00)
    assert exc.value.status_code == 429
    assert "AI budget ceiling reached" in exc.value.detail

    # Budget summary verification
    summary = get_ai_budget_summary(db)
    assert summary["limit_usd"] == 10.0
    assert summary["spent_usd"] == 9.5
    assert summary["remaining_usd"] == 0.5
    assert summary["percentage_used"] == 95.0

    # Age record2 outside the 3-hour window
    record2.created_at = utcnow() - timedelta(seconds=settings().ai_budget_window_seconds + 60)
    db.commit()

    # Budget should now have recovered (only record1 at 2.50 is active)
    summary_recovered = get_ai_budget_summary(db)
    assert summary_recovered["spent_usd"] == 2.5
    assert summary_recovered["remaining_usd"] == 7.5

    # New reservation should succeed now
    record3 = check_and_reserve_budget(db, purpose="after_recovery", estimated_cost=1.00)
    assert record3.status == "reserved"


def test_ai_budget_endpoint(client, db):
    # Empty spend summary
    res = client.get("/api/ai/budget")
    assert res.status_code == 200
    data = res.json()
    assert data["limit_usd"] == 10.0
    assert data["model"] == settings().openrouter_model


def test_approved_domains_management(client, db):
    # Verify default seed
    seed_domain = db.scalar(select(ApprovedDomain).where(ApprovedDomain.domain == "www.keychron.com"))
    assert seed_domain is not None
    assert is_domain_approved(db, "https://www.keychron.com/products/keyboard") is True
    assert is_domain_approved(db, "https://evil.com/phishing") is False

    admin_headers = {"x-demo-admin-token": settings().demo_admin_token}

    # Add domain
    res = client.post("/api/demo/approved-domains", json={"domain": "shop.example.com", "display_name": "Partner store"}, headers=admin_headers)
    assert res.status_code == 200
    added = res.json()
    assert added["domain"] == "shop.example.com"
    assert is_domain_approved(db, "https://shop.example.com/item/1") is True

    # List domains
    res = client.get("/api/demo/approved-domains", headers=admin_headers)
    assert res.status_code == 200
    domains = [d["domain"] for d in res.json()]
    assert "shop.example.com" in domains
    assert "www.keychron.com" in domains

    # Delete domain
    res = client.delete(f"/api/demo/approved-domains/{added['id']}", headers=admin_headers)
    assert res.status_code == 200
    assert is_domain_approved(db, "https://shop.example.com/item/1") is False


def test_checkout_demo_queue_and_safeguards(client, db, factory, tmp_path, monkeypatch):
    from app import worker
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(settings(), "screenshot_storage_path", str(tmp_path))

    # Create product and permission
    product = Product(
        id="demo-keychron",
        name="Keychron K2 Pro",
        variant="RGB",
        category="Keyboard",
        seller="Keychron",
        condition="new",
        price_cents=8900,
        original_cents=8900,
        shipping_cents=0,
        tax_basis_points=625,
        currency="USD",
        source_kind="merchant",
        source_url="https://www.keychron.com/products/keychron-m3-wireless-mouse?variant=12345",
        image_url="https://www.keychron.com/image.png",
        stock_status="in_stock",
        stock=10,
        price_checked_at=utcnow()
    )
    db.add(product)
    db.flush()

    permission = Permission(
        id="demo-perm",
        owner="user",
        product_id=product.id,
        variant="RGB",
        seller="Keychron",
        condition="new",
        currency="USD",
        quantity=1,
        max_total_cents=9500,
        expires_at=utcnow() + timedelta(days=1),
        status="active"
    )
    db.add(permission)
    card = VirtualCard(
        permission_id=permission.id,
        nessie_account_id="acct_123",
        last4="9876",
        limit_cents=9500,
        status="issued"
    )
    db.add(card)
    db.commit()

    admin_headers = {"x-demo-admin-token": settings().demo_admin_token}

    # Execute checkout demo runner with mocked playwright page to test strict submission prevention & masking
    with patch("app.worker.sync_playwright") as mock_pw:
        mock_browser = Mock()
        mock_context = Mock()
        mock_page = Mock()
        mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.locator.return_value.count.return_value = 0
        mock_page.frames = []
        mock_page.url = "https://www.keychron.com/checkouts/cn/test123"
        mock_page.evaluate.return_value = "normal checkout page"
        mock_page.screenshot.side_effect = lambda path, **kwargs: Path(path).write_bytes(b"FAKE_PNG_BYTES")

        # Trigger checkout demo via admin endpoint (runs background task with mock)
        res = client.post("/api/demo/checkout-demo", json={"permission_id": permission.id}, headers=admin_headers)
        assert res.status_code == 200
        demo_run = res.json()

    db.expire_all()
    updated_run = db.get(CheckoutDemoRun, demo_run["id"])
    assert updated_run.status == "completed"
    assert updated_run.is_submitted is False
    assert updated_run.screenshot_path is not None

    # Check evidence listing endpoint
    res = client.get("/api/demo/checkout-demos", headers=admin_headers)
    assert res.status_code == 200
    runs = res.json()
    assert any(r["id"] == demo_run["id"] for r in runs)

    # Check evidence screenshot image endpoint
    res = client.get(f"/api/screenshots/{demo_run['id']}")
    assert res.status_code == 200
    assert res.content == b"FAKE_PNG_BYTES"

    # Check /screenshots HTML gallery
    res = client.get("/screenshots")
    assert res.status_code == 200
    assert "Checkout Automation Evidence Gallery" in res.text
    assert "NOT SUBMITTED" in res.text


def test_unconfigured_openrouter_returns_503(client, monkeypatch):
    monkeypatch.setattr(settings(), "openrouter_api_key", "")
    monkeypatch.setattr(settings(), "xai_api_key", "")
    res = client.post("/api/rules/parse", json={"text": "Buy Keychron keyboard under $80"})
    assert res.status_code == 503
    assert "manual" in res.json()["detail"]


def test_price_drop_triggers_checkout_demo_run(db, factory, monkeypatch):
    from app import worker
    monkeypatch.setattr(worker, "SessionLocal", factory)

    product = Product(
        id="keychron-12345",
        name="Keychron K2 Pro",
        variant="RGB",
        category="Keyboard",
        seller="Keychron",
        condition="new",
        price_cents=6000,
        original_cents=8900,
        shipping_cents=0,
        tax_basis_points=0,
        currency="USD",
        source_kind="merchant",
        source_url="https://www.keychron.com/products/keychron-m3-wireless-mouse?variant=12345",
        image_url="https://www.keychron.com/image.png",
        stock_status="in_stock",
        stock=10,
        price_checked_at=utcnow()
    )
    db.add(product)
    db.flush()

    permission = Permission(
        id="price-drop-perm",
        owner="user",
        product_id=product.id,
        variant="RGB",
        seller="Keychron",
        condition="new",
        currency="USD",
        quantity=1,
        max_total_cents=7500,
        expires_at=utcnow() + timedelta(days=2),
        checkout_mode="sandbox",
        sandbox_total_cents=6000,
        purchase_mode="ask_first",
        status="active"
    )
    db.add(permission)
    card = VirtualCard(
        permission_id=permission.id,
        nessie_account_id="acct_pd",
        last4="4321",
        limit_cents=7500,
        status="issued"
    )
    db.add(card)
    db.commit()

    # Run worker scan
    worker.scan()

    with factory() as check_db:
        demo = check_db.scalar(
            select(CheckoutDemoRun).where(
                CheckoutDemoRun.permission_id == permission.id,
                CheckoutDemoRun.trigger_type == "price_drop"
            )
        )
        assert demo is not None
        assert demo.status == "queued"
        assert demo.product_id == product.id
