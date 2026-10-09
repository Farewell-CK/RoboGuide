#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 RESULTS_ROOT [ACTIVE_STATE_JSON]" >&2
  exit 2
fi

results_root=$1
active_state=${2:-}
remote=${SYNC_REMOTE:-origin}
target_branch=${SYNC_TARGET_BRANCH:-codex/e2-coherent-gpt6-sol}
output=docs/codex-sync/LIVE_STATUS.md
repo_root=$(git rev-parse --show-toplevel)
current_branch=$(git branch --show-current)

if [[ -z "$current_branch" || "$current_branch" == "$target_branch" ]]; then
  echo "run this publisher from a dedicated sync branch/worktree" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
  echo "sync worktree is not clean; refusing to mix unrelated changes" >&2
  exit 1
fi

lock_path=$(git rev-parse --git-path codex-live-status.lock)
exec 9>"$lock_path"
if ! flock -n 9; then
  echo "another live-status publisher is active" >&2
  exit 1
fi

git fetch "$remote" "$target_branch"
target_ref="$remote/$target_branch"
git merge --ff-only "$target_ref"
expected_target=$(git rev-parse "$target_ref")

render_args=(
  --repo "$repo_root"
  --results-root "$results_root"
  --output "$output"
)
if [[ -n "$active_state" ]]; then
  render_args+=(--active-state "$active_state")
fi
python3 "$repo_root/tools/codex-sync/render_live_status.py" "${render_args[@]}"

git diff --check -- "$output"
if grep -Ein '(github_pat_|ghp_[A-Za-z0-9]|sk-[A-Za-z0-9]|authorization:|bearer[[:space:]]|api[_-]?key)' "$output"; then
  echo "possible secret detected in generated status; refusing to commit" >&2
  exit 1
fi
if [[ -z "$(git status --porcelain -- "$output")" ]]; then
  echo "LIVE_STATUS.md is already current"
  exit 0
fi

git add -- "$output"
if [[ "$(git diff --cached --name-only)" != "$output" ]]; then
  echo "staged paths are not limited to $output" >&2
  exit 1
fi
git commit -m "docs: update recovery live status"

git fetch "$remote" "$target_branch"
actual_target=$(git rev-parse "$target_ref")
if [[ "$actual_target" != "$expected_target" ]]; then
  echo "target branch changed from $expected_target to $actual_target; not pushing" >&2
  exit 3
fi

# This is deliberately not a force push. Git rejects a concurrent remote update.
git push "$remote" "HEAD:refs/heads/$target_branch"
