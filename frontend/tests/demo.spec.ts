import { test, expect, type Page } from '@playwright/test';
const mouseId = 'keychron-44549379883097';
const mouseUrl = `https://www.keychron.com/products/keychron-m3-wireless-mouse?variant=${mouseId}`;

async function checkLink(page: Page, url = mouseUrl, soldOut = false) {
  await page.route('**/api/catalog/inspect', async (route) => {
    const response = await page.request.get('/api/products');
    const products = (await response.json()).filter((product: { id: string }) =>
      product.id.endsWith(new URL(url).searchParams.get('variant') || mouseId));
    await route.fulfill({ json: { status: 'complete', message: 'Verified', products: soldOut
      ? products.map((product: object) => ({ ...product, can_request: false, stock_status: 'sold_out' }))
      : products } });
  });
  await page.getByRole('button', { name: 'Start a watch' }).click();
  await page.getByLabel('Product URL').fill(url);
  await page.getByRole('button', { name: 'Check product link' }).click();
}


test('sold-out variants cannot be watched from a pasted link', async ({page}) => {
  await page.goto('/');
  await expect(page.locator('.product-card')).toHaveCount(0);
  await checkLink(page, 'https://www.keychron.com/products/keychron-c3-pro-qmk-via-wired-mechanical-keyboard?variant=40574854037593');
  await expect(page.getByRole('dialog')).toContainText('Sold out');
  await expect(page.getByRole('button', {name:'Watch this item'})).toBeDisabled();
  const response = await page.request.post('/api/permissions', {data:{product_id:'keychron-40574854037593',
    max_total_cents:4000,expires_at:new Date(Date.now()+86400000).toISOString()}});
  expect(response.status()).toBe(409);
});

test('real-item sandbox needs approval, records one banking receipt and can cancel',async({page})=>{
  await page.goto('/');
  await checkLink(page);
  await page.getByRole('button', {name:'Watch this item'}).click();
  await page.getByLabel('Experience').selectOption('sandbox');
  await expect(page.getByRole('dialog')).toContainText('No real money moves and no merchant order is placed');
  await page.getByLabel('Maximum total, including tax & shipping (USD)').fill('70');
  await page.getByLabel('Sandbox final total (USD, test scenario)').fill('50');
  await page.getByRole('button',{name:'Review purchase rules'}).click();
  await expect(page.getByLabel('Ask me first',{exact:false})).toBeChecked();
  await page.getByRole('button',{name:'Activate sandbox permission'}).click();
  await expect(page.locator('.watch-row')).toContainText('Card ••••');
  await page.getByRole('button',{name:'Approve purchase',exact:true}).click();
  await expect(page.locator('.watch-row')).toContainText('purchased',{timeout:60000});
  await page.getByRole('button',{name:'Activity',exact:true}).click();
  await expect(page.locator('.receipt')).toHaveCount(1);
  await expect(page.locator('.receipt')).toContainText('SANDBOX-');
  await expect(page.locator('.receipt')).toContainText('$50.00');
  await expect(page.getByText('No retailer order has been placed.',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:/Cancel purchase/}).click();
  await expect(page.locator('.receipt')).toContainText('cancelled');
  await page.reload();
  await page.getByRole('button',{name:'Activity',exact:true}).click();
  await expect(page.locator('.receipt')).toHaveCount(1);
});

test('Gemini rules update the real product visual and budget',async({page})=>{
  await page.route('**/api/session',route=>route.fulfill({json:{grok_configured:true,payments:'simulation'}}));
  await page.route('**/api/rules/parse',route=>route.fulfill({json:{product_id:mouseId,max_total_cents:4500,
    quantity:1,expires_at:new Date(Date.now()+86400000).toISOString()}}));
  await page.goto('/');
  await checkLink(page);
  await page.getByRole('button', {name:'Watch this item'}).click();
  await page.getByLabel('Describe it to Gemini',{exact:false}).fill('Watch the Keychron M3 under $45 for one day');
  await page.getByRole('button',{name:'Fill my rules'}).click();
  await expect(page.locator('#budget')).toHaveValue('45');
  await expect(page.locator('.selected-product .lucide-mouse')).toBeVisible();
  await expect(page.locator('.selected-product')).toContainText('Keychron');
});

test('assistant matches are actionable real products',async({page})=>{
  await page.route('**/api/session',route=>route.fulfill({json:{grok_configured:true,payments:'simulation'}}));
  await page.route('**/api/assistant',route=>route.fulfill({json:{message:'An available Keychron mouse.',
    tools:[{tool:'find_product',result:[{id:mouseId}]}]}}));
  await page.goto('/');
  await page.getByRole('button',{name:'Shopping Assistant',exact:true}).click();
  await page.getByLabel('Your message').fill('Find an available Keychron mouse');
  await page.getByRole('button',{name:'Ask Gemini',exact:true}).click();
  const matches=page.getByLabel('Product matches');
  await expect(matches).toContainText('Keychron M3');
  await matches.getByRole('button',{name:'Watch this item'}).click();
  await expect(page.getByRole('dialog')).toContainText('Keychron M3');
});

test('start watch explains unavailable stock',async({page})=>{
  await page.goto('/');
  await expect(page.getByText('HACKATHON PREVIEW',{exact:false})).toHaveCount(0);
  await checkLink(page, mouseUrl, true);
  await expect(page.getByRole('dialog')).toContainText('Sold out');
  await expect(page.getByRole('button',{name:'Watch this item'})).toBeDisabled();
});

test('Gemini results keep sold-out variants visible and disabled',async({page})=>{
  await page.route('**/api/session',route=>route.fulfill({json:{grok_configured:true,payments:'simulation'}}));
  await page.route('**/api/assistant',route=>route.fulfill({json:{message:'Available and sold-out variants.',
    tools:[{tool:'find_product',result:[{id:mouseId},{id:'keychron-40574854037593'}]}]}}));
  await page.goto('/');
  await page.getByRole('button',{name:'Shopping Assistant',exact:true}).click();
  await page.getByLabel('Your message').fill('Show all Keychron stock states');
  await page.getByRole('button',{name:'Ask Gemini',exact:true}).click();
  const matches=page.getByLabel('Product matches');
  await expect(matches.locator('.grok-product')).toHaveCount(2);
  await expect(matches.locator('.grok-product').filter({hasText:'C3 Pro'}).getByRole('button')).toBeDisabled();
  await expect(matches.locator('.grok-product').filter({hasText:'M3'}).getByRole('button')).toBeEnabled();
});

test('mobile and desktop overview fit without browser errors',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setViewportSize({width:390,height:844});await page.goto('/');
  await expect(page.locator('.product-card')).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({path:'test-results/dealone-real-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1050});
  await page.screenshot({path:'test-results/dealone-real-desktop.png',fullPage:true});
  expect(errors).toEqual([]);
});

test('red and black theme keeps overview text readable',async({page})=>{
  await page.goto('/');
  await expect(page.locator('.product-card')).toHaveCount(0);
  const colors=await page.locator(':root').evaluate(el=>({red:getComputedStyle(el).getPropertyValue('--brand-red').trim(),ink:getComputedStyle(el).getPropertyValue('--ink').trim()}));
  expect(colors).toEqual({red:'#c8102e',ink:'#171717'});
  expect(await page.locator('.page-title p').first().evaluate(el=>parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(16);
  expect(await page.locator('.page-title h1').first().evaluate(el=>parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(20);
});
