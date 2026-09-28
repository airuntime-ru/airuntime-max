#!/bin/sh
set -eu

TOKEN="${AIRUNTIME_TELEGRAM_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
if [ -z "$TOKEN" ]; then
  echo "ops-bot: AIRUNTIME_TELEGRAM_BOT_TOKEN not set — idle (bot disabled)"
  exec sleep infinity
fi

exec python -m ops_bot
