import { test, expect } from '@playwright/test';

test('watch, authorize, detect a drop, and buy exactly once', async ({ page, request }) => {
  const token = process.env.DEMO_ADMIN_TOKEN || 'local-demo-admin';
  await request.post('/api/demo/products/headphones/price', {
    headers: { 'x-demo-admin-token': token },
    data: { price_cents: 19900, shipping_cents: 0 },
  });
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'Good things come to those who watch.' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Watch this item' }).first().click();
  await page.getByLabel('Maximum total, including tax & shipping (USD)').fill('180');
  await page.getByRole('button', { name: 'Review purchase rules' }).click();
  await expect(page.getByRole('heading', { name: 'Your rules. Your call.' })).toBeVisible();
  await page.getByLabel('Buy automatically').check();
  await page.getByRole('button', { name: 'Activate automatic purchase' }).click();
  await expect(
    page.locator('.watch-row').filter({ hasText: 'Studio Wireless Headphones' }),
  ).toContainText('active');
  await expect(
    page.locator('.watch-row').filter({ hasText: 'Studio Wireless Headphones' }),
  ).toContainText('Card ••••');
  await request.post('/api/demo/products/headphones/price', {
    headers: { 'x-demo-admin-token': token },
    data: { price_cents: 15900 },
  });
  await expect(
    page.locator('.watch-row').filter({ hasText: 'Studio Wireless Headphones' }),
  ).toContainText('purchased', { timeout: 60000 });
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await expect(page.locator('.receipt')).toHaveCount(1);
  await expect(page.locator('.receipt')).toContainText('completed');
  await expect(page.locator('.receipt')).toContainText('$168.94');
  await expect(page.locator('.receipt')).toContainText('DO-');
  await page.reload();
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await expect(page.locator('.receipt')).toHaveCount(1);
  await request.post('/api/demo/products/headphones/price', {
    headers: { 'x-demo-admin-token': token },
    data: { price_cents: 19900 },
  });
});

test('mobile overview fits viewport and Grok configuration is honest', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'Good things come to those who watch.' }),
  ).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: 'test-results/dealone-mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'Grok assistant', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'A good deal starts with a conversation.' }),
  ).toBeVisible();
  await expect(
    page.getByText('Payments in this preview are simulated.', { exact: false }),
  ).toBeVisible();
});

test('desktop overview renders without browser errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.goto('/');
  await expect(page.locator('.product-card')).toHaveCount(4);
  await expect(page.getByText('Hidden-instruction demo', { exact: false })).toBeVisible();
  await page.screenshot({ path: 'test-results/dealone-desktop.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('ask me first notifies without auto-buying', async ({ page, request }) => {
  const token = process.env.DEMO_ADMIN_TOKEN || 'local-demo-admin';
  await request.post('/api/demo/products/keyboard/price', {
    headers: { 'x-demo-admin-token': token },
    data: { price_cents: 12900, shipping_cents: 0 },
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Watch this item' }).nth(1).click();
  await page.getByLabel('Maximum total, including tax & shipping (USD)').fill('150');
  await page.getByRole('button', { name: 'Review purchase rules' }).click();
  await expect(page.getByLabel('Ask me first')).toBeChecked();
  await page.getByRole('button', { name: 'Activate watch · ask me first' }).click();
  const row = page.locator('.watch-row').filter({ hasText: 'Mechanical Keyboard' });
  await expect(row).toContainText('active');
  await expect(row).toContainText('Ask me first');
  await request.post('/api/demo/products/keyboard/price', {
    headers: { 'x-demo-admin-token': token },
    data: { price_cents: 9900 },
  });
  await expect(page.getByRole('button', { name: 'Activity', exact: true })).toBeVisible();
  await expect(async () => {
    await page.getByRole('button', { name: 'Activity', exact: true }).click();
    await expect(page.locator('.notice').filter({ hasText: 'Price matched your rules' })).toBeVisible();
  }).toPass({ timeout: 60000 });
  await expect(page.locator('.receipt')).toHaveCount(0);
  await page.getByRole('button', { name: 'Watchlist', exact: true }).click();
  const matched = page.locator('.watch-row').filter({ hasText: 'Mechanical Keyboard' });
  await expect(matched).toContainText('active');
  await matched.getByRole('button', { name: 'Approve purchase' }).click();
  await expect(matched).toContainText('purchased', { timeout: 60000 });
});
