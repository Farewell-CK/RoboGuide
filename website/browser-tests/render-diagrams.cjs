/* Regenerate committed SVG assets from the maintained Mermaid source using the vendored runtime. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, 'docs/images/architecture');
const runtime = 'website/custom_theme/javascripts/vendor/mermaid-11.12.2.min.js';
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const manifest = { renderer: runtime, renderer_sha256: digest(fs.readFileSync(path.join(root, runtime))), diagrams: [] };
const names = ['overview', 'execution', 'deployment', 'habitat-evaluation'];
const titles = {
  zh: ['当前总体架构', '执行与持久化顺序', '进程与存储拓扑', 'Habitat 与 Eval Harness 接入', '决策与承诺流程'],
  en: ['Current system architecture', 'Execution and durability order', 'Process and storage topology', 'Habitat and Eval Harness integration', 'Decision and commitment flow'],
};
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1800, height: 1200 } });
  await page.setContent('<!doctype html><html><head><meta charset="utf-8"></head><body><div id="diagram"></div></body></html>');
  await page.addScriptTag({ path: path.join(root, runtime) });
  await page.evaluate(() => mermaid.initialize({
    startOnLoad: false, theme: 'base', deterministicIds: true, deterministicIDSeed: 'roboguide-architecture',
    htmlLabels: false, fontFamily: 'Noto Sans CJK SC, Microsoft YaHei, Arial, sans-serif',
    flowchart: { useMaxWidth: false, htmlLabels: false, nodeSpacing: 40, rankSpacing: 60, padding: 22 },
    sequence: { useMaxWidth: false, actorMargin: 45, messageMargin: 35 },
    themeVariables: {
      primaryColor: '#edf4ff', primaryTextColor: '#182b45', primaryBorderColor: '#5077a9',
      lineColor: '#536980', secondaryColor: '#f4f7fb', tertiaryColor: '#ffffff',
      clusterBkg: '#f5f8fc', clusterBorder: '#94a7bf', edgeLabelBackground: '#ffffff',
    },
  }));
  const seen = new Set();
  for (const [locale, suffix, assetSuffix] of [['zh', '', ''], ['en', '.en', '-english']]) {
    for (const file of [`docs/architecture/v2/current-architecture${suffix}.md`, `docs/architecture/v2/README${suffix}.md`]) {
      const source = fs.readFileSync(path.join(root, file), 'utf8');
      const codes = [...source.matchAll(/^```mermaid\n([\s\S]*?)\n```/gm)].map(match => match[1].trim());
      for (let index = 0; index < codes.length; index += 1) {
        const code = codes[index];
        const sha = digest(code);
        if (seen.has(sha)) continue;
        seen.add(sha);
        const atlas = file.includes('current-architecture');
        const name = atlas ? names[index] : 'decision-pipeline';
        if (!name) throw new Error(`Unexpected source diagram: ${file} ${index}`);
        const title = titles[locale][atlas ? index : 4];
        const stem = name + assetSuffix;
        const result = await page.evaluate(async ({ id, code }) => {
          const result = await mermaid.render(id, code);
          document.querySelector('#diagram').innerHTML = result.svg;
          await document.fonts.ready;
          const svg = document.querySelector('#diagram svg');
          const box = svg.viewBox.baseVal;
          return { svg: result.svg, width: Math.ceil(box.width), height: Math.ceil(box.height) };
        }, { id: 'rg-' + stem, code });
        const svg = result.svg.replace(/(<svg\b[^>]*>)/, `$1<metadata>roboguide-source-sha256:${sha}</metadata>`);
        const svgPath = `docs/images/architecture/${stem}.svg`;
        const mmdPath = name === 'overview'
          ? `docs/images/roboguide-current-architecture${assetSuffix}.mmd`
          : `docs/images/architecture/${stem}.mmd`;
        fs.writeFileSync(path.join(root, svgPath), svg + '\n');
        if (name !== 'overview') fs.writeFileSync(path.join(root, mmdPath), code + '\n');
        manifest.diagrams.push({ name, locale, title, source: file, index, source_sha256: sha,
          svg: svgPath, svg_sha256: digest(svg + '\n'), mmd: mmdPath, width: result.width, height: result.height });
        console.log(`Rendered ${stem}: ${result.width} x ${result.height}`);
      }
    }
  }
  fs.writeFileSync(path.join(root, 'website/diagram-assets.json'), JSON.stringify(manifest, null, 2) + '\n');
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
