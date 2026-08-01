# Contributing to Robonix Client

Robonix Client accepts contributions from people who can review, explain,
license, validate, and maintain the changes they submit.

## Prepare and validate a focused change

Base work on the latest `main` branch and keep each commit limited to one
independently reviewable problem. Run the Python tests and, for frontend
changes, rebuild the bundle and run the Playwright suite:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[audio]'
.venv/bin/python -m unittest discover -s tests -v

cd frontend
npm ci
npm run build
npm run test:visual
```

## Commit messages

Use Conventional Commits:

```text
<type>(optional scope): <imperative description>
```

## Human authorship and AI assistance

The Git author must be a human contributor. The committer must also be human,
except for GitHub's `GitHub <noreply@github.com>` web-flow identity when GitHub
applies a human-reviewed merge.

Repository automation and AI coding agents must not be named as authors or
committers. Do not attribute authorship or responsibility to an AI agent with
`Co-authored-by`, `Co-developed-by`, `Signed-off-by`, `Reviewed-by`,
`Tested-by`, `Acked-by`, or `Suggested-by` trailers.

The human author must review and understand the complete change, confirm its
provenance and license compatibility, run appropriate validation, and accept
full responsibility for correctness, security, and maintenance.

AI assistance is permitted. Disclose material assistance only with:

```text
Assisted-by: AGENT_NAME:MODEL_VERSION [TOOL ...]
```

For example:

```text
Assisted-by: Codex:gpt-5.6
```

`Assisted-by` identifies a tool, not a person: do not include an email address.
CI checks every commit introduced by a pull request or protected-branch push.
After a force-push makes the previous base unavailable, it audits the complete
history reachable from the new head.

Run the same check locally with:

```bash
python3 scripts/check_commit_authorship.py --base origin/main --head HEAD
```
