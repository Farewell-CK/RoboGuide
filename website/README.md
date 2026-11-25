# RoboGuide documentation publication

The repository’s current bilingual guides are canonical. `website/pages/` contains route wrappers;
`source-pages.json` maps each established public route to one source under `docs/`. Edit the source
in both languages. The site mirrors it and retains the previous routes and anchors.

## Build and inspect

```bash
python website/sync_docs.py
uvx --from mkdocs --with-requirements website/requirements.txt mkdocs build --strict -f website/mkdocs.yml
python website/check_site.py
PYTHONPATH=website python -m unittest discover -s website/tests
```

Only tracked inputs are published. Stage new sources before building. `website/docs/`, `website/site/`,
`tmp/` and browser dependencies are local artifacts. Source links bind the checkout commit; the reviewed
content SHA/date and source-derived contract versions remain in `content-baseline.json`.

`doc_inventory.py` verifies complete coverage of the original Git tree, exact preserved Git blobs,
reading destinations and bilingual route mappings. Historical ADRs, acceptance and audit originals
retain their original paths and content. Rewritten sources have exact pre-refresh snapshots.

## API-generated architecture figures

The current atlas has eight topics in Chinese and English. `diagram-assets.json` binds each actual
PNG to its prompt, requested model, reviewed code, dimensions and digest. The imagegen skill CLI calls
`/v1/images/generations` using the explicitly selected latest model. The installed CLI is not copied
or modified. Resolve credentials in the calling process only; never put them in publication inputs.

Generation briefs, prompts and actual outcomes live beside the PNGs under the dated architecture
asset directory. Check labels, every required edge and authority boundary visually before marking
an asset reviewed. Store a returned model identity only if the API actually supplies one.

Normal synchronization refuses pending, changed or unreviewed images. When API permissions block
images, an explicit local prose preview is available:

```bash
python website/sync_docs.py --text-preview
uvx --from mkdocs --with-requirements website/requirements.txt mkdocs build --strict -f website/mkdocs.yml
python website/check_site.py --text-preview
```

This preview has `publication_ready=false`, displays no substitute drawings and cannot pass normal
publication checks. A model-list entry is not a successful image generation. Historical Mermaid
sources remain original evidence and are displayed as literal source text; the site uses no diagram
renderer. Current figures remain visible and open at full size with JavaScript disabled.

## Browser and packaged-resource checks

Install the locked browser dependencies with `npm ci --ignore-scripts --prefix website/browser-tests`,
then install Chromium using Playwright. Serve `website/site/` on loopback and run `npm test` from
`website/browser-tests/`. `DOCS_URL` selects the loopback origin and `DOCS_SCREENSHOTS` selects an output
folder. Only explicit local prose validation uses `DOCS_TEXT_PREVIEW=1`.
`DOCS_BROWSER_EXECUTABLE` optionally selects an installed Chromium-family browser for local checks;
CI uses Playwright's locked Chromium installation.

Checks cover Chinese/English navigation, compatibility routes and anchors, search, mobile/tablet/
desktop widths, light/dark themes, generated-image loading/enlargement and no-JavaScript reading.
Packaged runbooks retain their installed filenames; `pyproject.toml` points to the relocated full
sources. Run affected workbench runbook tests and inspect wheel resource contents after moves.

## Delivery

Run the repository’s Ruff, strict mypy and function-documentation checks for changed website code,
publication regressions, strict bilingual build and rendered-link checks. Runtime and physical
acceptance remain independent. Deliver a reviewed documentation branch/PR; publication must use
a clean approved source commit and verified actual build metadata.

## Production branch isolation

The production Worker must build `main` only. In Cloudflare Workers & Pages → `roboguide-docs` →
Settings → Builds → Branch control, set the production branch to `main`. Keep preview builds disabled
until the preview deploy command has been checked to avoid promotion to the production Worker.
Branch controls are a Cloudflare account setting; `wrangler.jsonc` does not select the production Git
branch. A repository change alone cannot fix an older branch's deploy command.

On 2026-10-22, the production build metadata changed from main's `495a6d2` to development commit
`6c5a099`, and both new atlas routes returned 404. This was verified from actual `build-info.json` and
HTTP responses; it was not merely a missing Mermaid render. After each publication, compare the live
source to the intended main commit and check the actual V2/atlas routes and image assets.

Cloudflare documents the separate production/preview branch controls in its
[Build branches guide](https://developers.cloudflare.com/workers/ci-cd/builds/build-branches/).
Do not perform the irreversible Worker Previews migration as part of a documentation update without
separate authorization; disabling preview builds is reversible.
