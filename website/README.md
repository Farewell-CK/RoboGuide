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
