#!/usr/bin/env bash
set -euo pipefail
cd ~/kbo-monitor

# Load secrets from local .env (git-ignored) if present.
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

/usr/bin/python3 monitor.py
