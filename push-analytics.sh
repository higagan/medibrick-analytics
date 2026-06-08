#!/bin/bash
# Push analytics data from local monitoring to Supabase via API

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_URL="https://medibrick-analytics.vercel.app/api/analytics/check"

# Read the latest log file
LOG_DIR="/Users/gagandeep/.openclaw/workspace/plausible/logs"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/analytics_$TODAY.json"

if [ ! -f "$LOG_FILE" ]; then
  echo "No log file for today"
  exit 1
fi

# Read last line (latest check)
LAST_LINE=$(tail -1 "$LOG_FILE")

if [ -z "$LAST_LINE" ]; then
  echo "Empty log file"
  exit 1
fi

# Push to API
curl -s -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d "$LAST_LINE" 2>&1

echo "Analytics data pushed"
