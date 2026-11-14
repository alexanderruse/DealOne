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
  await page.getByRole('button', { name: 'Practice checkout', exact: true }).click();
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
  await page.getByRole('button', { name: 'Practice checkout', exact: true }).click();
  await expect(page.locator('.product-card')).toHaveCount(3);
  await page.screenshot({ path: 'test-results/dealone-desktop.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('Grok changes the selected product artwork and real watches explain merchant checkout', async ({ page }) => {
  const product = { id: 'keychron-test', name: 'Keychron M3 Wireless Mouse', variant: 'Black / 1K', seller: 'Keychron',
    price_cents: 4999, original_cents: 4999, total_cents: 4999, category: 'Mouse', color: 'merchant',
    source_kind: 'merchant', source_url: 'https://www.keychron.com/products/keychron-m3-wireless-mouse?variant=1234',
    image_url: null, icon: 'mouse', price_checked_at: new Date().toISOString() };
  await page.route('**/api/products', async route => {
    const response = await route.fetch();
    await route.fulfill({ json: [...(await response.json()).filter((p: {source_kind?: string}) => p.source_kind !== 'merchant'), product] });
  });
  await page.route('**/api/session', route => route.fulfill({ json: { grok_configured: true } }));
  await page.route('**/api/rules/parse', route => route.fulfill({ json: { product_id: product.id,
    max_total_cents: 4500, quantity: 1, expires_at: new Date(Date.now() + 86400000).toISOString() } }));
  await page.goto('/');
  await expect(page.locator('.product-card').filter({hasText: product.name})).toBeVisible();
  await expect(page.getByRole('link', {name: 'View at Keychron'})).toHaveAttribute('href', product.source_url);
  await page.getByRole('button', {name: 'Practice checkout', exact: true}).click();
  await page.getByRole('button', {name: 'Watch this item'}).first().click();
  await page.getByLabel('Describe it to Grok', {exact: false}).fill('Watch the Keychron mouse under $45 for a day');
  await page.getByRole('button', {name: 'Fill my rules'}).click();
  await expect(page.locator('.modal > p').first()).toContainText(product.name);
  await expect(page.locator('#watch-product')).toHaveValue(product.id);
  await expect(page.locator('#budget')).toHaveValue('45');
  await expect(page.locator('.selected-product .lucide-mouse')).toBeVisible();
  await expect(page.locator('.modal')).toContainText('Automatic merchant checkout is not connected');
});

test('assistant tool results produce actionable matching product visuals', async ({page}) => {
  await page.route('**/api/session', route => route.fulfill({json:{grok_configured:true}}));
  await page.route('**/api/assistant', route => route.fulfill({json:{message:'Here are the supported headphones.',
    tools:[{tool:'find_product',result:[{id:'headphones'}]}]}}));
  await page.goto('/');
  await page.getByRole('button', {name:'Grok assistant',exact:true}).click();
  await page.getByLabel('Your message').fill('Find the headphones');
  await page.getByRole('button', {name:'Ask Grok',exact:true}).click();
  const matches=page.getByLabel('Grok product matches');
  await expect(matches).toContainText('Studio Wireless Headphones');
  await matches.getByRole('button', {name:'Watch this item'}).click();
  await expect(page.getByRole('dialog')).toContainText('Studio Wireless Headphones');
});
