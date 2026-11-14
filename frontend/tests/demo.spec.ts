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
  await page.getByRole('button', { name: 'Activate automatic purchase' }).click();
  await expect(
    page.locator('.watch-row').filter({ hasText: 'Studio Wireless Headphones' }),
  ).toContainText('active');
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
  await expect(page.locator('.product-card')).toHaveCount(3);
  await page.screenshot({ path: 'test-results/dealone-desktop.png', fullPage: true });
  expect(errors).toEqual([]);
});
