#!/usr/bin/env bash
# Decide whether a scheduled nightly attempt should do the work, and for which trading day.
#
# GitHub delivers this repository's schedules hours late (5-7 h measured), so the workflow
# asks for an attempt every 30 minutes through the day and this gate lets exactly one
# through: the first that starts after the 18:30 Warsaw cut-off, or any attempt that finds
# the previous trading day's digest still missing. Every other attempt ends in seconds.
#
# The trading day follows pipeline.nightly_date: before the 17:05 close it is the last
# weekday whose session has finished.
#
# Prints "date=YYYY-MM-DD", "go=true|false" and "reason=..." (GITHUB_OUTPUT format).
# Usage: nightly_gate.sh <schedule|workflow_dispatch> <requested date|today>
# Env:   NOW      "YYYY-MM-DD HH:MM" Warsaw time, for tests (default: the clock)
#        DIGEST_EXISTS  command that succeeds if the digest of $1 is on main
#                       (default: asks the GitHub API; needs GH_TOKEN and GITHUB_REPOSITORY)
set -euo pipefail
event="${1:-schedule}"
requested="${2:-today}"

if [ -n "${NOW:-}" ]; then
  today="${NOW%% *}"
  hm="${NOW##* }"
  hm="${hm/:/}"
else
  today=$(TZ=Europe/Warsaw date +%F)
  hm=$(TZ=Europe/Warsaw date +%H%M)
fi

digest_exists() {
  if [ -n "${DIGEST_EXISTS:-}" ]; then
    $DIGEST_EXISTS "$1"
  else
    gh api "repos/${GITHUB_REPOSITORY}/contents/digests/${1:0:4}/${1:5:2}/${1:8:2}.md" \
      --silent >/dev/null 2>&1
  fi
}

day="$today"
if [ "$((10#$hm))" -lt 1705 ]; then day=$(date -u -d "$day -1 day" +%F); fi
while [ "$(date -u -d "$day" +%u)" -ge 6 ]; do day=$(date -u -d "$day -1 day" +%F); done

if [ "$event" != "schedule" ]; then
  if [ "$requested" != "today" ]; then day="$requested"; fi
  echo "date=$day"
  echo "go=true"
  echo "reason=started by hand"
  exit 0
fi

echo "date=$day"
if [ "$day" = "$today" ] && [ "$((10#$hm))" -lt 1835 ]; then
  echo "go=false"
  echo "reason=before the 18:30 cut-off"
elif digest_exists "$day"; then
  echo "go=false"
  echo "reason=the digest for $day is already out"
else
  echo "go=true"
  echo "reason=no digest for $day yet"
fi
