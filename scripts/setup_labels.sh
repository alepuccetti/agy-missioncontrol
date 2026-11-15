#!/usr/bin/env bash
set -euo pipefail

# Note: Epics are tracked via the GitHub Issue Type "Epic" (--type Epic),
# not via an "epic" label.
labels=(
  "agent-ready:0E8A16:Ready for autonomous agent execution"
  "blocked:D93F0B:Blocked by upstream dependencies or quota"
  "area/quota:1D76DB:Quota governor, sliding window and rate limiting"
  "area/manifest:5319E7:Task manifest parser, serializer, and TASKS.md"
  "area/runner:0052CC:Execution supervisor, verification, and detached runner"
  "area/cli:BFD4F2:CLI framework, configuration, and terminal UX"
)

for item in "${labels[@]}"; do
  IFS=":" read -r name color desc <<< "$item"
  echo "Syncing label: $name"
  gh label create "$name" --color "$color" --description "$desc" --force || true
done

# Ensure the legacy "epic" label is never left behind.
if gh label list --limit 100 --json name --jq '.[].name' | grep -qx "epic"; then
  echo "Deleting legacy label: epic (Epics use GitHub Issue Type 'Epic')"
  gh label delete epic --yes || true
fi
