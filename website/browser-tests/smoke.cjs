/* Check API-generated PNG delivery; this script never draws or replaces figures. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const base = process.env.DOCS_URL || 'http://127.0.0.1:39801';
const output = process.env.DOCS_SCREENSHOTS || path.join(os.tmpdir(), 'roboguide-docs-screenshots');
const preview = process.env.DOCS_TEXT_PREVIEW === '1';
const routes = ['/', '/architecture-tour/', '/getting-started/', '/status/',
  '/docs/architecture/v2/architecture-handbook/', '/docs/architecture/v2/current-architecture/',
  '/docs/architecture/v2/', '/docs/architecture/v2/system-dataflows/', '/docs/guides/workbench/'];

async function inspectFigures(page, route) {
  const figures = page.locator('article .rg-architecture-figure');
  const count = route.includes('/current-architecture/') ? 8 :
    (route === '/' || route === '/en/' || route.includes('/architecture-tour/') ||
      /\/v2\/$/.test(route) || route.includes('/architecture-handbook/')) ? 1 : 0;
  assert.equal(await figures.count(), count, 'figure count: ' + route);
  assert.equal(await page.locator('pre.mermaid, script[src*="mermaid"], script[src*="diagrams.js"]').count(), 0);
  if (preview) {
    assert.equal(await figures.locator('img').count(), 0, 'preview must not invent images');
    assert.equal(await page.locator('article .rg-pending-image').count(), count);
  } else if (count) {
    assert.equal(await figures.locator('img').count(), count);
    await page.waitForFunction(() => [...document.querySelectorAll('.rg-architecture-figure img')]
      .every(image => image.complete && image.naturalWidth > 0));
    assert.ok(await figures.locator('img').evaluateAll(images => images.every(image =>
      image.naturalWidth === Number(image.getAttribute('width')) &&
      image.naturalHeight === Number(image.getAttribute('height')) && image.alt.trim() &&
      image.getBoundingClientRect().width <= image.closest('figure').getBoundingClientRect().width + 1)));
    for (const href of await figures.locator('a > img').evaluateAll(images => images.map(image => image.parentElement.href))) {
      assert.match(new URL(href).pathname, /\.png$/);
    }
  }
  const locale = route.startsWith('/en/') ? 'en' : 'zh';
  for (const key of await figures.evaluateAll(nodes => nodes.map(node => node.dataset.diagram))) {
    assert.ok(key.endsWith(':' + locale), 'localized figure: ' + key);
  }
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, 'overflow: ' + route);
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.DOCS_BROWSER_EXECUTABLE || undefined });
  const errors = [];
  const external = [];
  const context = await browser.newContext({ reducedMotion: 'reduce' });
  await context.route('**/*', route => {
    const url = route.request().url();
    if (new URL(url).origin === new URL(base).origin) return route.continue();
    external.push(url);
    return route.abort();
  });
  const metadata = await (await context.request.get(base + '/build-info.json')).json();
  assert.equal(metadata.publication_ready, !preview, 'preview/publication mode must be explicit');
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 950 });
    for (const scheme of ['default', 'slate']) {
      for (const prefix of ['', '/en']) {
        for (const route of routes) {
          const target = prefix + route;
          const response = await page.goto(base + target, { waitUntil: 'networkidle' });
          assert.equal(response.status(), 200, target);
          await page.locator('article h1').waitFor();
          await page.evaluate(value => document.body.setAttribute('data-md-color-scheme', value), scheme);
          await inspectFigures(page, target);
          if (route === '/' || route.includes('/current-architecture/')) {
            const name = route === '/' ? 'home' : 'atlas';
            await page.screenshot({ path: path.join(output, name + '-' + width + '-' + scheme + (prefix ? '-en' : '-zh') + '.png'), fullPage: true });
          }
        }
      }
      console.log('Passed responsive figures: ' + width + 'px ' + scheme + (preview ? ' (text preview)' : ' (PNG)'));
    }
  }
  for (const prefix of ['', '/en']) {
    for (const name of ['domain-ports', 'mission', 'orchestration', 'control', 'runtime', 'state', 'integration-node-service', 'evaluation']) {
      const response = await page.goto(base + prefix + '/modules/' + name + '/', { waitUntil: 'networkidle' });
      assert.equal(response.status(), 200);
      assert.equal(await page.locator('pre.mermaid').count(), 0);
    }
  }
  await page.setViewportSize({ width: 1440, height: 950 });
  await page.goto(base + '/architecture-tour/', { waitUntil: 'networkidle' });
  const english = page.locator('.md-select__link', { hasText: 'English' });
  assert.match(await english.getAttribute('href'), /en\/architecture-tour\/?$/);
  await page.locator('.md-select > button').hover();
  await english.click();
  assert.match(new URL(page.url()).pathname, /^\/en\/architecture-tour\/?$/);
  await page.locator('[data-md-component="search-query"]').fill('Memory');
  await page.waitForFunction(() => document.querySelectorAll('.md-search-result__link').length > 0);
  await page.keyboard.press('Escape');
  await page.keyboard.press('Tab');
  assert.ok(await page.evaluate(() => document.activeElement !== document.body), 'keyboard focus');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + '/', { waitUntil: 'networkidle' });
  await page.locator('.md-header label[for="__drawer"]').click();
  assert.equal(await page.locator('#__drawer').isChecked(), true, 'mobile navigation');
  const staticContext = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  const staticPage = await staticContext.newPage();
  for (const prefix of ['', '/en']) {
    for (const name of ['current-architecture/', '']) {
      const route = prefix + '/docs/architecture/v2/' + name;
      const response = await staticPage.goto(base + route, { waitUntil: 'networkidle' });
      assert.equal(response.status(), 200);
      await inspectFigures(staticPage, route);
    }
  }
  if (!preview) {
    const [expanded] = await Promise.all([
      staticContext.waitForEvent('page'), staticPage.locator('.rg-architecture-figure > a').first().click(),
    ]);
    await expanded.waitForLoadState('load');
    assert.match(new URL(expanded.url()).pathname, /^\/en\/.*\.png$/);
    assert.equal(await expanded.locator('img').count(), 1, 'full PNG without JavaScript');
  }
  await staticContext.close();
  assert.deepEqual(errors, [], 'browser JavaScript errors');
  assert.deepEqual(external, [], 'unexpected external requests');
  await browser.close();
  console.log('Bilingual routes, language switch, search, keyboard, mobile navigation and no-JavaScript reading passed.');
})().catch(error => { console.error(error); process.exit(1); });
