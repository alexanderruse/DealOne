import { test, expect } from '@playwright/test';

test('large catalogs batch only watched histories and preserve evidence on transient errors', async ({ page }) => {
  const products = Array.from({ length: 500 }, (_, index) => ({
    id: `keyboard-${index}`, name: `Keyboard ${index}`, variant: 'Black', category: 'Keyboard',
    seller: 'Keychron', source_kind: 'merchant', price_cents: 5000, original_cents: 5000,
    total_cents: 5000, stock: 1, stock_status: 'in_stock', can_request: true, currency: 'USD',
    price_checked_at: new Date().toISOString(), icon: 'keyboard',
  }));
  const evidence = [{ id: 'proof', product_name: 'Keyboard proof', product_variant: 'Black',
    seller: 'Keychron', trigger_type: 'manual_user', status: 'completed', fields_completed: ['phone'],
    nessie_verified_limit_cents: 5000, has_screenshot: false, is_submitted: false,
    created_at: new Date().toISOString() }];
  const historyBatches: string[][] = [];
  let legacyHistoryRequests = 0;
  let evidenceRequests = 0;
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/prices')) {
      legacyHistoryRequests++;
      await route.abort();
      return;
    }
    if (path === '/api/price-history') {
      historyBatches.push(route.request().postDataJSON().product_ids);
      await route.fulfill({ json: {} });
      return;
    }
    if (path === '/api/demo/checkout-demos') {
      evidenceRequests++;
      await route.fulfill(evidenceRequests === 1 ? { json: evidence } : { status: 503, json: { detail: 'temporary' } });
      return;
    }
    const data = path === '/api/session' ? { ai_configured: true, payments: 'simulation' }
      : path === '/api/products' ? products
      : path === '/api/permissions' ? products.slice(0, 2).map((product) => ({ id: product.id, product_id: product.id, status: 'active', max_total_cents: 5000, product }))
      : path === '/api/payments/ledger' ? { charges: [], nessie: [] }
      : path === '/api/payments/card' || path === '/api/ai/budget' ? null : [];
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await expect(page.locator('.product-card')).toHaveCount(0);
  await page.getByRole('button', { name: 'Watchlist', exact: true }).click();
  await expect(page.locator('.watch-row')).toHaveCount(2);
  await expect.poll(() => historyBatches.length).toBe(1);
  expect(historyBatches[0]).toEqual(products.slice(0, 2).map((product) => product.id));
  expect(legacyHistoryRequests).toBe(0);
  await page.getByRole('button', { name: 'Evidence Gallery', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Keyboard proof' })).toBeVisible();
  await expect.poll(() => evidenceRequests, { timeout: 10000 }).toBeGreaterThan(1);
  await expect(page.getByRole('heading', { name: 'Keyboard proof' })).toBeVisible();
  expect(historyBatches).toHaveLength(1);
  expect(legacyHistoryRequests).toBe(0);
});
