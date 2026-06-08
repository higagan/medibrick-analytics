#!/bin/bash
# UptimeRobot setup script for Medibrick
# Creates a monitor that checks medibrick.com every 5 minutes

# NOTE: You need to create an UptimeRobot account first:
# 1. Go to https://uptimerobot.com
# 2. Sign up (free plan)
# 3. Go to Settings → API Settings
# 4. Copy your API key
# 5. Paste it below:

UPTIMEROBOT_API_KEY="YOUR_API_KEY_HERE"

if [ "$UPTIMEROBOT_API_KEY" = "YOUR_API_KEY_HERE" ]; then
  echo "❌ Please set your UptimeRobot API key first"
  echo "Get it from: https://uptimerobot.com/#settings"
  exit 1
fi

# Create monitor for medibrick.com
echo "Creating UptimeRobot monitor for medibrick.com..."

curl -s -X POST "https://api.uptimerobot.com/v2/newMonitor" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "api_key=$UPTIMEROBOT_API_KEY" \
  -d "format=json" \
  -d "type=1" \
  -d "url=https://medibrick.com" \
  -d "friendly_name=Medibrick.com" \
  -d "interval=300" \
  -d "alert_contacts=default" \
  2>&1 | python3 -m json.tool

echo ""
echo "Monitor created! Check https://uptimerobot.com/dashboard"
