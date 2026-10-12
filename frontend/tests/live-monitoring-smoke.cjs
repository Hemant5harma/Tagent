// Authorized deployment smoke test: real website, one-use login, read-only tabs.
const { chromium } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const { mkdirSync } = require('node:fs');
const { resolve } = require('node:path');

(async () => {
  const remote = 'cd /home/jarvis/trade-agent && .venv/bin/python -';
  const script = `from trade_agent.config import Settings
from trade_agent.db import Database, DashboardCacheRecord
from trade_agent.dashboard import issue_code
import json
db = Database(Settings().database_url)
with db.session() as session:
    url = session.get(DashboardCacheRecord, "dashboard_url").payload["url"]
print(json.dumps({"url": url, "code": issue_code(db)}))
`;
  const login = JSON.parse(execFileSync('ssh', ['-o', 'BatchMode=yes', 'jarvis@100.120.34.104', remote], {input:script, encoding:'utf8', timeout:20000}));
  const out = resolve(__dirname, '../../dist/live-monitoring');
  mkdirSync(out, {recursive:true});
  const browser = await chromium.launch({headless:true, executablePath:process.argv[2] || undefined});
  const page = await browser.newPage({viewport:{width:1440,height:1050}});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto(login.url, {waitUntil:'networkidle', timeout:30000});
    await page.getByLabel('Dashboard access code').fill(login.code);
    await page.getByRole('button', {name:/open my dashboard/i}).click();
    await page.getByRole('heading', {name:'The bigger picture.'}).waitFor();
    const checked = [];
    for (const name of ['Overview','Trades','Agent','System']) {
      await page.getByRole('button', {name, exact:true}).filter({visible:true}).click();
      await page.locator('.content').waitFor();
      if (name === 'Overview') await page.getByRole('heading', {name:'Manual and autonomous performance'}).waitFor();
      if (name === 'System') await page.getByText('Autonomy worker', {exact:true}).waitFor();
      if (name === 'Agent') {
        if (process.argv.includes('--timeframe-comparison')) {
          const preview = page.locator('.comparison-preview');
          await preview.locator('summary').filter({hasText:'Historical M5 / M15 / M30 preview'}).click();
          if (await preview.locator('.timeframe-comparison').count() !== 2) throw new Error('Expected previews for both symbols');
          if (await preview.locator('.trade-plan').count() !== 6) throw new Error('Expected three timeframe cards per symbol');
        }
        if (process.argv.includes('--data-health')) {
          await page.getByText('Research only — autonomous orders are disabled', {exact:true}).waitFor();
          for (const symbol of ['EURUSD', 'XAUUSD']) {
            await page.locator('.autonomy-readiness p').filter({hasText:symbol}).filter({hasText:'No fresh quote'}).first().waitFor();
          }
          await page.getByText('No demo execution grant is active.', {exact:true}).waitFor();
        }
        let candidates = page.locator('.autonomy-run').filter({hasText:'5/5 roles'});
        if (process.argv.includes('--verified-research')) candidates = candidates.filter({hasText:'Verified by code:'});
        if (process.argv.includes('--trade-plans')) candidates = candidates.filter({hasText:'Entry, stop-loss and take-profit plans'});
        const completed = candidates.first();
        await completed.locator('.decision').filter({hasText:/WAIT|BUY|SELL/}).waitFor();
        await completed.getByText('Read research reports and market evidence', {exact:true}).click();
        if (await completed.locator('.research-report h4').count() !== 6) throw new Error('Five role reports and market evidence were not rendered');
        if (process.argv.includes('--trade-plans')) {
          await completed.getByText('Entry, stop-loss and take-profit plans', {exact:true}).click();
          if (await completed.locator('.trade-plan').count() !== 2) throw new Error('Both directional plans must be visible');
          await completed.getByText('Stop-loss', {exact:true}).first().waitFor();
        }
      }
      await page.screenshot({path:resolve(out, name.toLowerCase()+'.png'),fullPage:true});
      checked.push(name);
    }
    if (errors.length) throw new Error('Website JavaScript errors: '+errors.join('; '));
    await page.setViewportSize({width:390,height:900});
    await page.getByRole('button', {name:'Overview',exact:true}).filter({visible:true}).click();
    if (!await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)) throw new Error('Mobile horizontal overflow');
    await page.screenshot({path:resolve(out,'mobile.png'),fullPage:true});
    if (process.argv.includes('--trade-plans')) {
      await page.getByRole('button', {name:'Agent',exact:true}).filter({visible:true}).click();
      const planned = page.locator('.autonomy-run').filter({hasText:'Entry, stop-loss and take-profit plans'}).first();
      if (!await planned.locator('.trade-plans').getAttribute('open').then(value => value !== null)) {
        await planned.getByText('Entry, stop-loss and take-profit plans', {exact:true}).click();
      }
      if (!await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)) throw new Error('Trade plans overflow on mobile');
      await planned.locator('.trade-plan').first().screenshot({path:resolve(out,'mobile-plans.png')});
    }
    if (process.argv.includes('--timeframe-comparison')) {
      await page.getByRole('button', {name:'Agent',exact:true}).filter({visible:true}).click();
      const preview = page.locator('.comparison-preview');
      if (await preview.getAttribute('open') === null) await preview.locator('summary').click();
      if (!await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)) throw new Error('Timeframe comparison overflows on mobile');
      await preview.screenshot({path:resolve(out,'mobile-timeframes.png')});
    }
    await page.evaluate(() => fetch('/dashboard-api/logout', {method:'POST'}));
    console.log(JSON.stringify({website:login.url, checked, mobile_width:390, javascript_errors:errors.length, screenshots:out, login_code_exposed:false}));
  } finally {
    await browser.close();
  }
})().catch(e => {console.error(e.message);process.exitCode=1;});
