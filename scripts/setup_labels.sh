#!/usr/bin/env bash
set -euo pipefail

labels=(
  "agent-ready:0E8A16:Ready for autonomous agent execution"
  "epic:3E4B9E:Top-level architectural epic"
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
