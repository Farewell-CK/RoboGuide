/* Browser validation only; the documentation site remains a Python-built static site. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.DOCS_URL || 'http://127.0.0.1:39801';
const output = process.env.DOCS_SCREENSHOTS || '/tmp/roboguide-docs-screenshots';
const routes = ['/', '/en/', '/architecture-tour/', '/en/architecture-tour/', '/getting-started/', '/en/getting-started/', '/status/', '/en/status/', '/docs/architecture/v2/architecture-handbook/', '/docs/architecture/v2/current-architecture/', '/en/docs/architecture/v2/current-architecture/', '/docs/architecture/v2/', '/en/docs/architecture/v2/'];
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const errors = [];
  let requests = 0;
  const context = await browser.newContext({ reducedMotion: 'reduce' });
  // The whole static site, including diagram rendering, must work without third-party CDNs.
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin === new URL(base).origin) return route.continue();
    requests += 1;
    console.error("Unexpected external request:", route.request().url());
    return route.abort();
  });
  await context.addInitScript(() => {
    // Material intentionally renders Mermaid in closed shadow roots. Retain references for
    // assertions without changing shadow mode or production behavior.
    window.__docsRoots = [];
    const attach = Element.prototype.attachShadow;
    Element.prototype.attachShadow = function(options) {
      const root = attach.call(this, options);
      window.__docsRoots.push(root);
      return root;
    };
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 950 });
    for (const scheme of ['default', 'slate']) {
      for (const route of routes) {
        const response = await page.goto(base + route, { waitUntil: 'networkidle' });
        assert.equal(response.status(), 200, `${width} ${scheme}: ${route}`);
        await page.locator('article h1').waitFor({timeout: 15000});
        await page.evaluate(value => document.body.setAttribute('data-md-color-scheme', value), scheme);
        await page.waitForFunction(() => !document.querySelector('pre.mermaid'));
        if (route.includes('architecture') || route === '/' || route === '/en/') {
          await page.waitForFunction(() => window.__docsRoots.some(root =>
            root.host.closest('article') && root.querySelector('svg')) ||
            [...document.querySelectorAll('.rg-architecture-figure img')].some(image => image.complete && image.naturalWidth > 0));
        }
        if (route.includes('/current-architecture/')) {
          assert.equal(await page.locator('.rg-architecture-figure img').count(), 4, route);
          await page.waitForFunction(() => [...document.querySelectorAll('.rg-architecture-figure img')]
            .every(image => image.complete && image.naturalWidth > 0));
          const fitting = await page.locator('.rg-architecture-figure img').evaluateAll(images =>
            images.every(image => image.getBoundingClientRect().width <= image.closest('figure').getBoundingClientRect().width + 1));
          assert.ok(fitting, `complete diagrams must fit the article: ${route}`);
          await page.locator('.rg-diagram-source summary').first().click();
          assert.ok(await page.locator('.rg-diagram-source').first().evaluate(details => details.open), route);
          await page.locator('.rg-diagram-source summary').first().click();
          await page.screenshot({ path: path.join(output, `architecture-${width}-${scheme}-${route.startsWith('/en/') ? 'en' : 'zh'}.png`), fullPage: true });
        }
        assert.equal(await page.locator('.error-icon, svg[aria-roledescription="error"]').count(), 0, route);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `${width}: ${route}`);
        if (route.includes('architecture') || route === '/' || route === '/en/') {
          assert.ok(await page.evaluate(() => window.__docsRoots.some(root =>
            root.host.closest('article') && root.querySelector('svg')) ||
            document.querySelectorAll('.rg-architecture-figure img').length > 0), `diagram missing: ${route}`);
        }
      }
      await page.goto(base + '/', { waitUntil: 'networkidle' });
      await page.evaluate(value => document.body.setAttribute('data-md-color-scheme', value), scheme);
      const ratios = await page.locator('.md-typeset .md-button').evaluateAll(buttons => {
        const luminance = rgb => rgb.map(v => v / 255).map(v =>
          v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
          .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
        const channels = value => value.match(/[\d.]+/g).map(Number);
        return buttons.map(button => {
          let element = button;
          let background;
          while (element) {
            const value = channels(getComputedStyle(element).backgroundColor);
            if (value.length === 3 || value[3] > 0) { background = value.slice(0, 3); break; }
            element = element.parentElement;
          }
          const foreground = channels(getComputedStyle(button).color).slice(0, 3);
          if (!background) {
            const probe = document.createElement('span');
            probe.style.backgroundColor = 'var(--md-default-bg-color)';
            document.body.append(probe);
            background = channels(getComputedStyle(probe).backgroundColor).slice(0, 3);
            probe.remove();
          }
          const a = luminance(foreground), b = luminance(background);
          return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
        });
      });
      assert.ok(ratios.every(value => value >= 4.5), `button contrast: ${scheme} ${ratios}`);
      await page.screenshot({ path: path.join(output, `home-${width}-${scheme}.png`), fullPage: true });
      console.log(`Passed responsive/diagram checks: ${width}px ${scheme}`);
    }
  }
  await page.setViewportSize({ width: 1440, height: 950 });
  for (const locale of ['', 'en/']) {
    for (const name of ['domain-ports', 'mission', 'orchestration', 'control', 'runtime', 'state', 'integration-node-service', 'evaluation']) {
      await page.goto(base + '/' + locale + 'modules/' + name + '/', { waitUntil: 'networkidle' });
      const expected = await page.locator('article pre.mermaid, article div.mermaid').count();
      if (expected) {
        await page.waitForFunction(count => window.__docsRoots.filter(root =>
          root.host.closest('article') && root.querySelector('svg')).length === count, expected);
      }
      assert.equal(await page.evaluate(() => window.__docsRoots.some(root =>
        root.querySelector('.error-icon'))), false, `module diagram: ${locale}${name}`);
    }
  }
  console.log('All bilingual module diagrams rendered.');
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
  assert.ok(await page.evaluate(() => document.activeElement !== document.body), 'keyboard focus missing');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + '/', { waitUntil: 'networkidle' });
  await page.locator('.md-header label[for="__drawer"]').click();
  assert.equal(await page.locator('#__drawer').isChecked(), true, 'mobile drawer');
  const staticContext = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  const staticPage = await staticContext.newPage();
  for (const prefix of ['', '/en']) {
    for (const name of ['current-architecture/', '']) {
      const response = await staticPage.goto(base + prefix + '/docs/architecture/v2/' + name, { waitUntil: 'networkidle' });
      assert.equal(response.status(), 200);
      assert.equal(await staticPage.locator('.rg-architecture-figure img').count(), name ? 4 : 2);
      await staticPage.waitForFunction(() => [...document.querySelectorAll('.rg-architecture-figure img')]
        .every(image => image.complete && image.naturalWidth > 0));
      assert.equal(await staticPage.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, 'static page overflow');
    }
  }
  const [expanded] = await Promise.all([
    staticContext.waitForEvent('page'),
    staticPage.locator('.rg-architecture-figure > a').first().click(),
  ]);
  await expanded.waitForLoadState('load');
  assert.equal(await expanded.locator('svg').count(), 1, 'full SVG without JavaScript');
  await staticContext.close();
  console.log('Architecture figures and enlargement work with JavaScript disabled.');
  assert.deepEqual(errors, [], 'browser JavaScript errors');
  assert.equal(requests, 0, 'unexpected external CDN requests');
  await browser.close();
  console.log('Language switch, search, keyboard and mobile drawer passed; zero external requests.');
})().catch(error => { console.error(error); process.exit(1); });
