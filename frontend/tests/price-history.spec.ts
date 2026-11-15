import { test, expect, Page } from '@playwright/test';

const mouseId = 'keychron-44549379883097';
const sample = (minute: number, price_cents: number) => ({
  price_cents, observed_at: new Date(Date.UTC(2026, 10, 15, 0, minute)).toISOString(),
});
const changing = [sample(0, 5999), sample(5, 5499), sample(20, 5299)];
const mouse = (page: Page) => page.locator('.product-card').filter({ hasText: 'Keychron M3' });

test('one batch plots real elapsed times and exposes exact observed values', async ({ page }) => {
  const bodies: { product_ids: string[] }[] = [];
  let legacyCalls = 0;
  await page.route('**/api/products/*/prices', route => { legacyCalls++; return route.abort(); });
  await page.route('**/api/price-history', route => {
    bodies.push(route.request().postDataJSON());
    return route.fulfill({ json: { [mouseId]: changing } });
  });
  await page.goto('/');
  const chart = mouse(page).locator('.price-history');
  await expect(chart).toBeVisible();
  await expect(chart).toContainText('Low $52.99');
  await expect(chart).toContainText('High $59.99');
  const xs = await chart.locator('circle').evaluateAll(nodes => nodes.map(node => Number(node.getAttribute('cx'))));
  // Five minutes is a quarter of the twenty-minute span, not the halfway sample.
  expect((xs[1] - xs[0]) / (xs[2] - xs[0])).toBeCloseTo(0.25, 3);
  await chart.getByText('View price observations').click();
  await expect(chart.locator('li')).toHaveCount(3);
  await expect(chart.locator('li').nth(1)).toContainText('$54.99');
  expect(bodies).toHaveLength(1);
  expect(bodies[0].product_ids.sort()).toEqual(['keychron-40574854037593', mouseId].sort());
  expect(legacyCalls).toBe(0);
  await page.screenshot({ path: 'test-results/inline-history-observation-fixture.png', fullPage: true });
});

test('empty, single and invalid observations do not render empty chart panels', async ({ page }) => {
  await page.route('**/api/price-history', route => route.fulfill({ json: {
    [mouseId]: [sample(0, 5999), {price_cents: -1, observed_at: 'bad'}],
    'keychron-40574854037593': [],
  } }));
  await page.goto('/');
  await expect(page.locator('.product-card')).toHaveCount(2);
  await expect(page.locator('.price-history')).toHaveCount(0);
  await expect(page.locator('.price-chart')).toHaveCount(0);
  await expect(mouse(page).getByRole('button', {name: 'Watch this item'})).toBeEnabled();
});

test('unchanged prices show a compact notice instead of a chart', async ({ page }) => {
  await page.route('**/api/price-history', route => route.fulfill({ json: {
    [mouseId]: [sample(0, 5999), sample(10, 5999), sample(20, 5999)],
  } }));
  await page.goto('/');
  await expect(mouse(page)).toContainText('No observed price change');
  await expect(page.locator('.price-history')).toHaveCount(0);
});

test('unavailable history does not break the catalog or watch dialog', async ({ page }) => {
  await page.route('**/api/price-history', route => route.fulfill({ status: 503, json: {detail: 'Unavailable'} }));
  await page.goto('/');
  await expect(page.locator('.product-card')).toHaveCount(2);
  await expect(page.locator('.price-history')).toHaveCount(0);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await mouse(page).getByRole('button', {name: 'Watch this item'}).click();
  await expect(page.getByRole('dialog')).toContainText('Keychron M3');
});

test('long observation gaps are not connected', async ({ page }) => {
  await page.route('**/api/price-history', route => route.fulfill({json: {
    [mouseId]: [sample(0, 5999), sample(5, 5499), sample(60, 5299)],
  }}));
  await page.goto('/');
  const chart = mouse(page).locator('.price-history');
  await expect(chart.locator('circle')).toHaveCount(3);
  await expect(chart.locator('polyline')).toHaveCount(1);
  expect((await chart.locator('polyline').getAttribute('points'))!.split(' ')).toHaveLength(2);
  await expect(chart).toContainText('Gaps over 30 minutes');
});

test('slow history times out while the product remains usable', async ({ page }) => {
  let aborted = false;
  page.on('requestfailed', request => {if (request.url().endsWith('/api/price-history')) aborted = true;});
  await page.route('**/api/price-history', () => {});
  await page.goto('/');
  await expect(page.locator('.product-card')).toHaveCount(2);
  await expect.poll(() => aborted, {timeout: 6000}).toBe(true);
  await expect(mouse(page).getByRole('button', {name: 'Watch this item'})).toBeEnabled();
  await expect(page.locator('.price-history')).toHaveCount(0);
});

test('inline history fits a mobile product card', async ({ page }) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.route('**/api/price-history', route => route.fulfill({json: {
    [mouseId]: [sample(0, 5999), sample(5, 5499), sample(20, 5299)],
  }}));
  await page.goto('/');
  await expect(mouse(page).locator('.price-history')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(mouse(page).getByRole('button', {name: 'Watch this item'})).toBeEnabled();
});
