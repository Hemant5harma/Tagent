import { test, expect } from '@playwright/test';

function snapshot() {
  const stamp = new Date().toISOString();
  return {
    generated_at: stamp, broker: { broker: 'Vantage', paused: true, observed_at: stamp,
      assisted_demo_accepted: true, account: { account_id: 'test4634', is_demo: true,
        currency: 'USD', balance: '10000', equity: '9995', margin_free: '9990' },
      state: { positions: [{ position_id: '12345678', symbol: 'EURUSD', side: 'buy', volume: '.01',
        entry_price: '1.10', stop_loss: '1.09', take_profit: '1.11', profit: '-5', opened_at: stamp }], orders: [] } },
    metrics: {net_realized_pnl:'0', winning_pnl:'0', losing_pnl:'0', closed_trades:0, breakevens:0, win_rate:null, profit_factor:null},
    equity:[{time:stamp,equity:'10000'}, {time:stamp,equity:'9995'}], drawdown:'5',
    drawdown_scope:'Sampled account-wide observations', coverage:{equity_since:stamp,since:stamp,until:stamp,unattributed_adjustments:'0'},
    closed_trades:[], proposals:[], events:[], activity:[],
    execution:{prepared:0, unknown:0, rejected:0, accepted:0, closed:0, cancelled:0},
    services:{limits:{status:'blocked',reason:'Demo loss circuit breaker is latched. Entries require operator review.'}},
  };
}

for (const width of [360, 390, 430, 1440]) {
  test(`all read-only sections fit ${width}px`, async ({page}) => {
    await page.setViewportSize({width, height:900});
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/dashboard-api/snapshot?*', route => route.fulfill({json:snapshot()}));
    await page.goto('/');
    await expect(page.getByRole('heading', {name:'The bigger picture.'})).toBeVisible();
    for (const tab of ['Overview','Trades','Agent','System']) {
      await page.getByRole('button', {name:tab, exact:true}).filter({visible:true}).click();
      await expect(page.locator('.content')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
      await page.screenshot({path:`test-results/${width}-${tab}.png`, fullPage:true});
    }
    expect(errors).toEqual([]);
    await expect(page.getByRole('button', {name:'Confirm trade'})).toHaveCount(0);
    await page.getByRole('button', {name:'Overview', exact:true}).filter({visible:true}).focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('button', {name:'Overview', exact:true}).filter({visible:true})).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', {name:'Trades', exact:true}).filter({visible:true})).toBeFocused();
  });
}

test('unauthorized login, stale data and disconnected state are explicit', async ({page}) => {
  await page.route('**/dashboard-api/snapshot?*', route => route.fulfill({status:401,json:{}}));
  await page.goto('/');
  await expect(page.getByLabel('Dashboard access code')).toBeVisible();
  await page.unroute('**/dashboard-api/snapshot?*');
  await page.route('**/dashboard-api/snapshot?*', route => {
    const data = snapshot(); data.broker.observed_at = '2020-01-01T00:00:00Z';
    return route.fulfill({json:data});
  });
  await page.reload();
  await expect(page.getByText('○ Data delayed')).toBeVisible();
  await page.unroute('**/dashboard-api/snapshot?*');
  await page.route('**/dashboard-api/snapshot?*', route => route.abort());
  await page.getByRole('button', {name:'Refresh', exact:true}).click();
  await expect(page.getByText('Connection interrupted. Displaying the last verified snapshot.')).toBeVisible();
});
