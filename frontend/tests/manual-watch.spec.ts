import { test, expect } from '@playwright/test';

const product = {
  id: 'keychron-2', name: 'Fixture keyboard', variant: 'Blue', seller: 'Keychron',
  source_url: 'https://www.keychron.com/products/fixture?variant=2',
  source_kind: 'merchant', price_cents: 5000, can_request: true, supports_checkout_demo: true,
};

test('manual links select the specified variant and Activity follows checkout evidence', async ({ page }) => {
  const now = new Date().toISOString();
  const run = {
    id: 'fixture', product_name: product.name, status: 'running', fields_completed: [],
    created_at: now, has_screenshot: false, has_payment_screenshot: false,
  };
  let finish = false;
  let polled = false;
  const old = { ...run, id: 'old', product_name: 'Older checkout', status: 'completed', has_screenshot: true };
  const finished = { ...run, status: 'completed', has_screenshot: true, has_payment_screenshot: true };
  await page.route('http://dealone.test/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith('/api/')) {
      const asset = path === '/' ? '/index.html' : path;
      return route.fulfill({ path: `dist${asset}` });
    }
    if (path === '/api/session') return route.fulfill({ json: { payments: 'simulation' } });
    if (path === '/api/demo/approved-domains') return route.fulfill({ json: [{ domain: 'www.keychron.com' }] });
    if (path === '/api/payments/ledger') return route.fulfill({ json: { charges: [], nessie: [] } });
    if (path === '/api/activity/checkout-runs') return route.fulfill({ json: [finish ? finished : run] });
    if (path === '/api/demo/checkout-demos') return route.fulfill({ json: finish ? [finished, old] : [old] });
    if (path === '/api/catalog/inspect') {
      const { url } = route.request().postDataJSON();
      if (new URL(url).hostname !== 'www.keychron.com') {
        return route.fulfill({ status: 422, json: { detail: 'Domain is not approved' } });
      }
      return route.fulfill({ json: { task_id: 'fixture', status: 'queued', message: 'Queued for verification', products: [] } });
    }
    if (path === '/api/catalog/tasks/fixture') {
      polled = true;
      return route.fulfill({ json: { task_id: 'fixture', status: 'complete', message: 'Verified', products: [
        product, { ...product, id: 'keychron-1', variant: 'Red', source_url: product.source_url.replace('=2', '=1') },
      ] } });
    }
    if (path.startsWith('/api/screenshots/')) {
      return route.fulfill({ contentType: 'image/png', body: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+XqRsAAAAASUVORK5CYII=', 'base64',
      ) });
    }
    return route.fulfill({ json: [] });
  });

  await page.goto('http://dealone.test/');
  await expect(page.locator('.product-card')).toHaveCount(0);
  await expect(page.locator('.profile-avatar')).toHaveCount(1);
  await page.getByRole('button', { name: 'Start a watch' }).click();
  await expect(page.getByRole('dialog')).toContainText('www.keychron.com');
  await page.getByLabel('Product URL').fill('https://unapproved.test/product');
  await page.getByRole('button', { name: 'Check product link' }).click();
  await expect(page.getByRole('alert')).toContainText('Domain is not approved');
  await page.getByLabel('Product URL').fill(product.source_url);
  await page.getByRole('button', { name: 'Check product link' }).click();
  await expect(page.getByRole('status')).toContainText('Queued');
  await page.getByRole('button', { name: 'Watch this item' }).click({ timeout: 10000 });
  expect(polled).toBe(true);
  await expect(page.getByRole('dialog')).toContainText('Fixture keyboard · Blue');
  await page.getByRole('button', { name: 'Close watch' }).click();

  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await expect(page.getByText('running', { exact: true })).toBeVisible();
  finish = true;
  await page.getByRole('button', { name: 'View checkout run', exact: true }).click({ timeout: 10000 });
  const latest = page.locator('#evidence-fixture');
  await expect(latest.getByAltText('Synthetic payment close-up')).toHaveAttribute('src', '/api/screenshots/fixture/payment');
  await expect(latest.getByAltText('Synthetic checkout demonstration evidence screenshot')).toBeVisible();
  await expect(page.locator('#evidence-old')).toContainText('Payment close-up unavailable');
  await expect(page.locator('#evidence-old img')).toHaveAttribute('src', '/api/screenshots/old');
});
