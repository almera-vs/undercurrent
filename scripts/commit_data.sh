#!/usr/bin/env bash
# Commit whatever changed under ./data to the `data` branch and push, retrying on races.
# Usage: scripts/commit_data.sh "message"
set -euo pipefail
msg="${1:-data update}"
cd data
git config user.name "undercurrent-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
if git diff --cached --quiet; then
  echo "no data changes"
  exit 0
fi
git commit -q -m "$msg"
for attempt in 1 2 3 4 5; do
  if git push -q origin HEAD:data; then
    echo "pushed: $msg"
    exit 0
  fi
  echo "push rejected (attempt $attempt); rebasing on origin/data"
  git fetch -q --depth=50 origin data
  git rebase -q origin/data
  sleep $((attempt * 5))
done
echo "could not push data after 5 attempts" >&2
exit 1
