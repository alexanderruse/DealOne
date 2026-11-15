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
    clock = Mock(wraps=worker.time)
    clock.monotonic.side_effect = [0, 16]
    monkeypatch.setattr(worker, "time", clock)

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
        owner=client.get("/api/session").json()["session_id"],
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

    # Execute checkout demo runner with mocked playwright page to test submission prevention and evidence capture
    with (patch("app.worker.sync_playwright") as mock_pw, patch("app.worker.open_demo_checkout"),
          patch("app.worker.advance_demo_delivery"), patch("app.worker.recover_demo_checkout")):
        mock_browser = Mock()
        mock_context = Mock()
        mock_page = Mock()
        mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.locator.return_value.count.return_value = 0
        mock_page.locator.return_value.all.return_value = []
        mock_page.frames = []
        mock_page.url = "https://www.keychron.com/checkouts/cn/test123"
        mock_page.evaluate.side_effect = lambda script, *args: True if args else "normal checkout page"
        mock_page.screenshot.side_effect = lambda path, **kwargs: Path(path).write_bytes(b"FAKE_PNG_BYTES")

        # Trigger checkout demo via admin endpoint (runs background task with mock)
        res = client.post("/api/demo/checkout-demo", json={"permission_id": permission.id}, headers=admin_headers)
        assert res.status_code == 200
        demo_run = res.json()
        assert demo_run["trigger_type"] == "manual_admin"

    db.expire_all()
    updated_run = db.get(CheckoutDemoRun, demo_run["id"])
    assert updated_run.status == "blocked"
    assert "card_number" in updated_run.blocker_reason
    assert updated_run.is_submitted is False
    assert updated_run.screenshot_path is not None
    assert updated_run.screenshot_png == b"FAKE_PNG_BYTES"
    # Simulate the next request landing on another replica with no local PNG.
    Path(updated_run.screenshot_path).unlink()

    # Check evidence listing endpoint
    res = client.get("/api/demo/checkout-demos", headers=admin_headers)
    assert res.status_code == 200
    runs = res.json()
    assert any(r["id"] == demo_run["id"] for r in runs)
    assert next(r for r in runs if r['id'] == demo_run['id'])['has_screenshot'] is True
    assert all('screenshot_png' not in r for r in runs)

    # Check evidence screenshot image endpoint
    res = client.get(f"/api/screenshots/{demo_run['id']}")
    assert res.status_code == 200
    assert res.content == b"FAKE_PNG_BYTES"

    # Check /screenshots HTML gallery
    res = client.get("/screenshots")
    assert res.status_code == 200
    assert "Checkout Runs" in res.text
    assert "NOT SUBMITTED" in res.text
    from fastapi.testclient import TestClient

    deletion_url = f"/api/demo/checkout-demos/{demo_run['id']}"
    with TestClient(client.app) as other_session:
        assert not other_session.get("/api/demo/checkout-demos").json()[0]["can_delete"]
        assert other_session.delete(deletion_url).status_code == 404
        assert other_session.delete(deletion_url, headers={"x-demo-admin-token": "wrong-token"}).status_code == 403
        assert other_session.get("/api/demo/checkout-demos", headers=admin_headers).json()[0]["can_delete"]
        assert other_session.delete(deletion_url, headers=admin_headers).status_code == 200
    assert client.get(f"/api/screenshots/{demo_run['id']}").status_code == 404
    body = {"permission_id": permission.id}
    with patch("app.worker.run_checkout_demo") as run_demo:
        wrong_token = client.post("/api/demo/checkout-demo", json=body,
                                  headers={"x-demo-admin-token": "wrong-token"})
        assert wrong_token.status_code == 403
        run_demo.assert_not_called()
        manual = client.post("/api/demo/checkout-demo", json=body)
        assert manual.json()["trigger_type"] == "manual_user"


@pytest.mark.parametrize(("quantity", "allowance", "error"), [
    (1, 4500, None), (2, 4500, "one unit"), (1, 4100, "exceeds the watch allowance"),
])
def test_checkout_cart_checks_quantity_allowance_and_discount(quantity, allowance, error):
    from app.worker import verify_demo_cart

    item = {"handle": "soap", "variant": "12345", "quantity": quantity, "subscription": False}
    cart = ([item], "USD", "45.00", None, 300)
    evidence = {}
    args = {"handle": "soap", "variant": "12345", "allowance": allowance, "evidence": evidence}
    if error:
        with pytest.raises(ValueError, match=error):
            verify_demo_cart(cart, **args)
    else:
        assert verify_demo_cart(cart, **args) == (item, None)
        assert evidence == {"cart_total_cents": 4500, "cart_discount_cents": 300}
        verify_demo_cart(cart, **{**args, "allowance": 4100}, admin_preview=True)
        assert "Admin preview continued" in evidence["preview_warning"]


@pytest.mark.parametrize("admin_preview", [False, True])
def test_missing_cart_only_allows_admin_preview_to_attempt_checkout(admin_preview):
    from app.worker import open_demo_checkout

    target = "https://merchant.example/products/soap"
    checkout = "https://merchant.example/checkout"
    page = Mock(url=target)
    page.request.get.return_value.ok = False

    def navigate(url, **kwargs):
        page.url = url

    page.goto.side_effect = navigate
    evidence = {}
    args = dict(linked_offer=False, allowance=4100, evidence=evidence,
                admin_preview=admin_preview, checkout_hosts={"merchant.example"})
    if admin_preview:
        open_demo_checkout(page, target, **args)
        assert page.url == checkout
        assert "did not provide a cart" in evidence["preview_warning"]
        assert "cart_total_cents" not in evidence
    else:
        with pytest.raises(ValueError, match="did not provide a cart"):
            open_demo_checkout(page, target, **args)
        assert page.url == target
        assert not evidence


def test_checkout_field_suggestions_only_fill_known_contact_controls(factory):
    from app.worker import complete_demo_contact_fields

    page = Mock()
    candidate = Mock()
    candidate.evaluate.return_value = "Town | text"
    target = candidate.element_handle.return_value
    target.input_value.side_effect = ["", "New York"]
    target.get_attribute.return_value = None
    page.locator.return_value.all.return_value = [candidate]
    ai_client = Mock()
    create = ai_client.with_options.return_value.chat.completions.create
    choice = Mock()
    choice.message.content = json.dumps(
        {"city": "0", "card_number": "0", "click": "Pay now"}
    )
    create.return_value.choices = [choice]
    completed = ["email", "first_name", "last_name", "address1", "address2", "postal_code"]
    with patch("app.worker.SessionLocal", factory), patch("app.ai.client", return_value=ai_client):
        complete_demo_contact_fields(page, {"city": "New York", "card_number": "synthetic"}, completed)

    assert completed[-1] == "city"
    assert "card_number" not in completed
    target.fill.assert_called_once_with("New York", timeout=2000)
    target.click.assert_not_called()


def test_checkout_recovery_observes_each_step_and_rejects_payment_actions():
    from app.worker import recover_demo_checkout

    page = Mock(url="https://merchant.example/checkouts/example", frames=[])
    page.locator.return_value.all.return_value = []
    steps = page.get_by_role.return_value.filter.return_value
    steps.all_text_contents.side_effect = [["Continue to delivery"], [], []]
    button = steps.nth.return_value.element_handle.return_value
    field = Mock()
    field.input_value.side_effect = ["", "New York"]
    field.get_attribute.return_value = None
    with (patch("app.worker.demo_contact_controls", side_effect=[
              ({}, {}), ({"0": "Town"}, {"0": field}),
          ]), patch("app.worker.checkout_suggestion", side_effect=[
              {"continue": 0}, {"fill": "city", "control": "0"},
          ]) as suggest):
        completed = []
        recover_demo_checkout(page, {"city": "New York"}, completed, {})

    assert completed == ["city"]
    button.click.assert_called_once()
    field.fill.assert_called_once_with("New York", timeout=2000)
    assert suggest.call_args_list[1].args[1]["inputs"] == {"0": "Town"}

    steps.all_text_contents.side_effect = None
    steps.all_text_contents.return_value = ["Pay now"]
    with patch("app.worker.checkout_suggestion", return_value={"continue": 0}):
        recover_demo_checkout(page, {}, [], {})
    button.click.assert_called_once()


def test_checkout_recovery_uses_one_masked_image_for_an_explicit_stage_link():
    from app.worker import explicit_checkout_stage_links, recover_demo_checkout

    url = "https://merchant.example/checkouts/example"
    page = Mock(url=url, frames=[Mock()])
    page.frames[0].locator.return_value.filter.return_value.count.return_value = 0
    page.get_by_role.return_value.filter.return_value.all_text_contents.return_value = []
    links = page.locator.return_value.all
    delivery, shipping, pay = Mock(), Mock(), Mock()
    delivery.evaluate.return_value = url + "?step=delivery"
    shipping.evaluate.return_value = url + "?step=shipping"
    pay.evaluate.return_value = url + "?step=pay"
    links.side_effect = [[delivery, pay], [delivery, shipping, pay]]
    assert explicit_checkout_stage_links(page) == [("delivery", url + "?step=delivery")]
    page.screenshot.return_value = b"fake jpeg"
    with patch("app.worker.checkout_suggestion", return_value={"go": 0}) as suggest:
        recover_demo_checkout(page, {}, [], {})
    suggest.assert_called_once()
    assert suggest.call_args.kwargs["screenshot"] == b"fake jpeg"
    assert page.screenshot.call_args.kwargs["mask"] == [page.frames[0].locator.return_value]
    page.goto.assert_called_once_with(url + "?step=delivery", wait_until="domcontentloaded", timeout=2500)


def test_masked_phone_input_is_retried_and_verified():
    from app.worker import fill_verified_digits
    loc = Mock()
    loc.input_value.side_effect = ["(555) 019-9", "(201) 555-0199"]
    loc.get_attribute.return_value = None
    assert fill_verified_digits(loc, "2015550199") is True
    loc.press_sequentially.assert_called_once_with("2015550199", delay=50)


def test_invalid_phone_is_not_marked_complete():
    from app.worker import fill_verified_digits
    loc = Mock()
    loc.input_value.return_value = "(201) 555-0199"
    loc.get_attribute.return_value = "true"
    assert fill_verified_digits(loc, "2015550199") is False


def test_flash_38_cost_fallback(monkeypatch):
    from types import SimpleNamespace
    from app.ai import extract_cost
    monkeypatch.setattr(settings(), "openrouter_model", "google/gemini-3.8-flash")
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=1000))
    assert extract_cost(response) == 0.0045


@pytest.mark.parametrize("nested_frame", [False, True])
def test_payment_input_ignores_proxy_and_scrolls_into_view(nested_frame):
    from html import escape
    from playwright.sync_api import sync_playwright
    from app.worker import rendered_payment_input
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 800, "height": 600})
        inputs = '''<input name="expiry_month" style="width:2px;height:2px;padding:0;border:0">
            <input name="expiry" style="width:200px;height:40px">'''
        if nested_frame:
            page.set_content(f'<div style="height:1500px"></div><iframe srcdoc="{escape(inputs, quote=True)}"></iframe>')
            frame = page.frames[1]
            frame.wait_for_selector('input[name="expiry"]')
        else:
            page.set_content('<div style="height:1500px"></div>' + inputs)
            frame = page.main_frame
        field = rendered_payment_input(frame, 'input[name*="expiry"]')
        assert field is not None
        assert field.get_attribute("name") == "expiry"
        assert 0 <= field.bounding_box()["y"] < 600
        browser.close()


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
