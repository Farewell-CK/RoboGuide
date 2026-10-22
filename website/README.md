# RoboGuide documentation site

The site is a bilingual MkDocs Material publication of the repository's architecture and evidence.
It does not define a second runtime contract. The reader handbook and accepted ADRs remain linked
from the V2 source of truth. Maintained guides are paired Chinese/English; historical ADRs, audits
and acceptance files retain their original language, with bilingual topic summaries and indexes.

## Build and inspect

From the repository root:

```bash
python website/sync_docs.py
uvx --from mkdocs --with-requirements website/requirements.txt mkdocs build --strict -f website/mkdocs.yml
python website/check_site.py
PYTHONPATH=website python -m unittest discover -s website/tests
```

For local preview, serve the generated `website/site/` on a free loopback port. The generated
`website/docs/`, `website/site/` and optional browser `node_modules` are ignored. Add new mirrored
source documents to Git before building: only tracked publication inputs are included. Meetings,
hidden files and temporary document trees are excluded. Private local experiments are never copied.

## Content and version review

- Edit `website/pages/` in both languages; missing pairs fail synchronization.
- Edit architecture source in `docs/architecture/v2/`, preserving authority and accepted ADR semantics.
- `content-baseline.json` retains the reviewed code SHA/date and source-derived contract identities.
  If an authoritative version changes, review affected prose/compatibility and update that manifest.
- `sync_docs.py` builds navigation, ADR catalogs and reference pages. It rewrites repository links,
  including old absolute workspace line links, to the exact build commit.
- `build-info.json` records source SHA, content-review SHA/date, actual dirty state and UTC build time.
  A dirty local preview is not a reproducible published commit. Publish only from a clean commit.
- `legacy-anchors.json` retains previous public anchors. Page routes remain stable; preserve this
  compatibility map when changing headings. New navigation labels were explicitly approved.

Do not modify historical results to match newer implementations. Distinguish accepted design,
implemented code, software validation and physical evidence. Use `status.md` for current boundaries,
not a handwritten ADR count or a copied protocol-version table.

## Browser verification

Optional Node dependencies serve browser validation only; site building and hosting remain Python/static.

```bash
npm ci --ignore-scripts --prefix website/browser-tests
cd website/browser-tests
npx playwright install chromium
```

Serve `website/site/` separately, then run `npm test` in `website/browser-tests/`. `DOCS_URL` selects
its loopback address (default `http://127.0.0.1:39801`); `DOCS_SCREENSHOTS` selects a fresh output folder.
The check blocks third-party network requests and validates diagrams, mobile/tablet/desktop widths,
light/dark themes, search, language switching, keyboard focus and the mobile drawer.

Mermaid 11.12.2 is vendored with its MIT license to avoid runtime CDN failures. Its exact upstream
artifact is `https://unpkg.com/mermaid@11.12.2/dist/mermaid.min.js`; no external font or diagram requests
are required. Keep the version, license and template path together when upgrading.

Maintained architecture diagrams are also committed as SVGs and displayed directly in the article,
with a full-size link and expandable/downloadable Mermaid source. They remain visible with JavaScript
disabled. After changing a registered Mermaid fence, regenerate its assets:

```bash
node website/browser-tests/render-diagrams.cjs
```

`diagram-assets.json` binds each SVG and downloadable source to the original fenced source and vendored
renderer. Synchronization refuses changed source or asset bytes until regeneration. Ordinary unregistered
Mermaid diagrams continue to use Material's local renderer. Do not edit generated SVGs by hand.

## CI and publication

`.github/workflows/docs.yml` runs type/style/function-documentation checks, publication regressions,
strict bilingual build, rendered link validation and browser checks. It uploads the static preview
and screenshots as artifacts. The existing Cloudflare Workers configuration continues serving
`website/site/`; keep build root and sync/build commands consistent with this guide.

Before merging a documentation branch, fetch the current target and review changes since the
content baseline. Resolve in the isolated worktree and rerun affected checks. Do not reset, stash,
clean, switch or consume uncommitted files in another session's checkout.

After publication, read `/build-info.json`, both homepages, architecture pages and search/assets.
Compare the deployed source SHA with the intended commit. A successful upload alone does not prove
that the deployment uses the expected source. Keep the preceding verified commit for rollback.

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
