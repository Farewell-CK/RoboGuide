# Codex Recovery Status Sync

This directory implements the first phase of the public synchronization mechanism. It reads completed Recovery evidence, writes `docs/codex-sync/LIVE_STATUS.md`, and can publish only that generated file from a dedicated Git worktree.

## Safety boundary

- The renderer reads result artifacts but never starts, stops, or modifies an experiment.
- Only whitelisted verdict, timeline, and provenance fields are rendered. Raw logs, prompts, Provider responses, paths, and credentials are not copied.
- Recovery Protocol and official Task Success are separate results.
- `publish-live-status.sh` requires a clean dedicated worktree, commits only `LIVE_STATUS.md`, fetches twice, compares the target SHA, and uses a normal non-force push. Concurrent target changes stop publication.
- Do not run the publisher from the primary experiment worktree.

## Dedicated worktree

Create this once after the phase-one implementation has been merged into the experiment branch:

```bash
git fetch origin codex/e2-coherent-gpt6-sol
git worktree add -b codex/e2-live-status-sync \
  ../RoboGuide-live-sync origin/codex/e2-coherent-gpt6-sol
```

The active-run state belongs outside the repository and outside formal result directories. An outer supervisor can update it before launch and after process exit, outside measured experiment time:

```bash
python3 tools/codex-sync/record_active_run.py \
  --state-file /private/local/state/codex-sync/active-run.json \
  --status running \
  --public-task env4/task17 \
  --run-id task17-f1-rep1 \
  --fault-profile f1-node-loss \
  --roboguide-commit "$(git rev-parse HEAD)"
```

After a key state change or after the run has finalized `fault-verdict.json`, publish from the dedicated worktree:

```bash
tools/codex-sync/publish-live-status.sh \
  /private/local/results/roboguide-recovery \
  /private/local/state/codex-sync/active-run.json
```

No timer or watcher is installed by this change. Wiring the two commands into an outer experiment supervisor must happen only after the active Recovery harness changes are settled. The hook must execute before launch or after final evidence/checksum creation, never inside the measured action/LLM interval.

## Tests

```bash
python3 -m pytest -q tools/codex-sync/test_render_live_status.py
bash -n tools/codex-sync/publish-live-status.sh
```
