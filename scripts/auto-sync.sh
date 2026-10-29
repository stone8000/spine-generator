#!/usr/bin/env bash
# Keeps this computer up to date with main: git pull, npm ci when the lockfile changed, then refreshes the
# global rule block (scripts/sync-rules.sh). install-local.sh runs it at login and every hour on macOS.
# Only pulls when the checkout is on main with no local changes, so work in progress is never touched.
set -uo pipefail
KIT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$KIT" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
echo "== $(date '+%F %T')"

branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$branch" != "main" ]; then
  echo "略過 pull：目前在分支 $branch，不是 main"
elif [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "略過 pull：main 上有未提交的改動"
else
  before="$(git rev-parse HEAD)"
  if git pull --ff-only --quiet origin main; then
    after="$(git rev-parse HEAD)"
    if [ "$before" != "$after" ]; then
      echo "更新 ${before:0:7} → ${after:0:7}"
      if ! git diff --quiet "$before" "$after" -- package-lock.json; then npm ci --no-audit --no-fund --silent; fi
    fi
  else
    echo "git pull 失敗（網路或分支分岔），下次再試"
  fi
fi
"$KIT/scripts/sync-rules.sh"
