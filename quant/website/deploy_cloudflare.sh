#!/usr/bin/env bash
# Foreground direct upload, with bounded recovery from transient Pages errors.
set -euo pipefail

rollout_directory="${1:-_site}"
for rollout_attempt in 1 2 3; do
  echo "Cloudflare Pages deployment attempt ${rollout_attempt}/3"
  if npx --yes wrangler@4.158.0 pages deploy "$rollout_directory" \
      --project-name=qliba158 --branch=main --skip-caching --commit-dirty=true; then
    exit 0
  else
    rollout_exit=$?
  fi
  if [[ "$rollout_attempt" == 3 ]]; then
    exit "$rollout_exit"
  fi
  sleep 10
done
