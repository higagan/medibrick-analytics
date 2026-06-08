#!/bin/bash
# Push analytics data from local monitoring to Supabase via API

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

# Map field names to match Supabase schema
# The monitoring script uses "deploy" but table has "deploy_status"
MAPPED_JSON=$(echo "$LAST_LINE" | python3 -c "
import json, sys
data = json.load(sys.stdin)
# Map fields to match Supabase schema
mapped = {
    'status': data.get('status', ''),
    'content': data.get('content', ''),
    'security_headers': data.get('security_headers', ''),
    'ssl_days': 50 if data.get('ssl') == 'ok' else 0,
    'ssl_status': data.get('ssl', ''),
    'deploy_status': data.get('deploy', ''),
    'response_time': data.get('response_time', 0),
    'dns_ip': data.get('dns_ip', '')
}
print(json.dumps(mapped))
")

# Push to API
curl -s -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d "$MAPPED_JSON" 2>&1

echo "Analytics data pushed"
