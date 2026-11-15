import { test, expect } from '@playwright/test';
const mouseId = 'keychron-44549379883097';

test('only real products appear and sold-out variants cannot be requested', async ({page}) => {
  await page.goto('/');
  await expect(page.locator('.product-card')).toHaveCount(2);
  await expect(page.getByText('DealOne Demo Store', {exact:false})).toHaveCount(0);
  const soldOut=page.locator('.product-card').filter({hasText:'Keychron C3 Pro'});
  await expect(soldOut).toContainText('Sold out');
  await expect(soldOut.getByRole('button',{name:'Watch this item'})).toBeDisabled();
  const response=await page.request.post('/api/permissions',{data:{product_id:'keychron-40574854037593',
    max_total_cents:4000,expires_at:new Date(Date.now()+86400000).toISOString()}});
  expect(response.status()).toBe(409);
  await page.locator('.product-card').filter({hasText:'Keychron M3'}).getByRole('button',{name:'Watch this item'}).click();
  const choices=await page.locator('#watch-product option').allTextContents();
  expect(choices.every(text=>text.includes('Keychron'))).toBeTruthy();
  expect(choices.some(text=>text.includes('C3 Pro'))).toBeFalsy();
});

test('real-item sandbox needs approval, records one banking receipt and can cancel',async({page})=>{
  await page.goto('/');
  await page.getByRole('button',{name:/Try (Nessie|banking) sandbox/}).click();
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

test('Grok rules update the real product visual and budget',async({page})=>{
  await page.route('**/api/session',route=>route.fulfill({json:{grok_configured:true,payments:'simulation'}}));
  await page.route('**/api/rules/parse',route=>route.fulfill({json:{product_id:mouseId,max_total_cents:4500,
    quantity:1,expires_at:new Date(Date.now()+86400000).toISOString()}}));
  await page.goto('/');
  await page.locator('.product-card').filter({hasText:'Keychron M3'}).getByRole('button',{name:'Watch this item'}).click();
  await page.getByLabel('Describe it to Grok',{exact:false}).fill('Watch the Keychron M3 under $45 for one day');
  await page.getByRole('button',{name:'Fill my rules'}).click();
  await expect(page.locator('#budget')).toHaveValue('45');
  await expect(page.locator('#watch-product')).toHaveValue(mouseId);
  await expect(page.locator('.selected-product .lucide-mouse')).toBeVisible();
  await expect(page.locator('.selected-product')).toContainText('Keychron');
});

test('assistant matches are actionable real products',async({page})=>{
  await page.route('**/api/session',route=>route.fulfill({json:{grok_configured:true,payments:'simulation'}}));
  await page.route('**/api/assistant',route=>route.fulfill({json:{message:'An available Keychron mouse.',
    tools:[{tool:'find_product',result:[{id:mouseId}]}]}}));
  await page.goto('/');
  await page.getByRole('button',{name:'Grok assistant',exact:true}).click();
  await page.getByLabel('Your message').fill('Find an available Keychron mouse');
  await page.getByRole('button',{name:'Ask Grok',exact:true}).click();
  const matches=page.getByLabel('Grok product matches');
  await expect(matches).toContainText('Keychron M3');
  await matches.getByRole('button',{name:'Watch this item'}).click();
  await expect(page.getByRole('dialog')).toContainText('Keychron M3');
});

test('start watch explains unavailable stock and preview badge is removed',async({page})=>{
  await page.route('**/api/products',async route=>{
    const response=await route.fetch();
    const products=await response.json();
    await route.fulfill({json:products.map((p:object)=>({...p,can_request:false,stock_status:'sold_out',stock:0}))});
  });
  await page.goto('/');
  await expect(page.getByText('HACKATHON PREVIEW',{exact:false})).toHaveCount(0);
  await page.getByRole('button',{name:'Start a watch',exact:true}).click();
  const picker=page.getByRole('dialog');
  await expect(picker).toContainText('No recently verified, available variants');
  await expect(picker.getByRole('button',{name:'Watch this item'}).first()).toBeDisabled();
  await expect(picker.getByRole('button',{name:'Check merchant availability'})).toBeEnabled();
});

test('Grok results keep sold-out variants visible and disabled',async({page})=>{
  await page.route('**/api/session',route=>route.fulfill({json:{grok_configured:true,payments:'simulation'}}));
  await page.route('**/api/assistant',route=>route.fulfill({json:{message:'Available and sold-out variants.',
    tools:[{tool:'find_product',result:[{id:mouseId},{id:'keychron-40574854037593'}]}]}}));
  await page.goto('/');
  await page.getByRole('button',{name:'Grok assistant',exact:true}).click();
  await page.getByLabel('Your message').fill('Show all Keychron stock states');
  await page.getByRole('button',{name:'Ask Grok',exact:true}).click();
  const matches=page.getByLabel('Grok product matches');
  await expect(matches.locator('.grok-product')).toHaveCount(2);
  await expect(matches.locator('.grok-product').filter({hasText:'C3 Pro'}).getByRole('button')).toBeDisabled();
  await expect(matches.locator('.grok-product').filter({hasText:'M3'}).getByRole('button')).toBeEnabled();
});

test('mobile and desktop real catalog fit without browser errors',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setViewportSize({width:390,height:844});await page.goto('/');
  await expect(page.locator('.product-card')).toHaveCount(2);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({path:'test-results/dealone-real-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1050});
  await page.screenshot({path:'test-results/dealone-real-desktop.png',fullPage:true});
  expect(errors).toEqual([]);
});

test('store discovery displays real listing previews and queued variant checks',async({page})=>{
  const task={task_id:'search-task',store:'epomaker',status:'complete',message:'Found a real keycap listing',products:[],
    listings:[{handle:'epomaker-aegissil-keycaps-set',name:'EPOMAKER AegisSil Keycaps Set',seller:'Epomaker',
      source_url:'https://epomaker.com/products/epomaker-aegissil-keycaps-set',image_url:null,price_from_cents:1500}]};
  await page.route('**/api/catalog/discover',async route=>{
    expect(route.request().postDataJSON()).toEqual({store:'epomaker',query:'silicone keycaps'});
    await route.fulfill({json:task});
  });
  await page.route('**/api/catalog/tasks/search-task',route=>route.fulfill({json:task}));
  const queued={...task,task_id:'inspect-task',status:'queued',listings:[],message:'Queued for merchant verification'};
  await page.route('**/api/catalog/inspect',route=>route.fulfill({json:queued}));
  await page.route('**/api/catalog/tasks/inspect-task',route=>route.fulfill({json:queued}));
  await page.goto('/');
  await page.getByLabel('Approved store',{exact:true}).selectOption('epomaker');
  await page.getByLabel('Search the store or paste a product link').fill('silicone keycaps');
  await page.getByRole('button',{name:'Find real products'}).click();
  const preview=page.locator('.discovery-card');
  await expect(preview).toContainText('EPOMAKER AegisSil Keycaps Set');
  await expect(preview).toContainText('From $15.00');
  await expect(preview.getByRole('button',{name:'Watch this item'})).toHaveCount(0);
  await preview.getByRole('button',{name:'Check variants & stock'}).click();
  await expect(page.getByLabel('Live merchant discovery')).toContainText('Queued for merchant verification');
});

test('red and black theme keeps product text readable',async({page})=>{
  await page.goto('/');
  await expect(page.locator('.product-card')).toHaveCount(2);
  const colors=await page.locator(':root').evaluate(el=>({red:getComputedStyle(el).getPropertyValue('--brand-red').trim(),ink:getComputedStyle(el).getPropertyValue('--ink').trim()}));
  expect(colors).toEqual({red:'#c8102e',ink:'#171717'});
  expect(await page.locator('.product-info > p').first().evaluate(el=>parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(16);
  expect(await page.locator('.product-info h3').first().evaluate(el=>parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(20);
});
