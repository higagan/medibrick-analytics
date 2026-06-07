#!/usr/bin/env bash
# MediBrick Leads - daily manual runner
# Run from your Mac whenever you want fresh leads.
#
# Usage:
#   ./update.sh                    # scrape all + push to Supabase
#   ./update.sh --dry-run          # scrape only, don't push
#   ./update.sh --only docthub     # one source
#   ./update.sh --city Mumbai      # different target city

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Pick the right python (look for venv first, then system)
if [ -d "venv" ]; then
  PYTHON="$SCRIPT_DIR/venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

if [ -z "$PYTHON" ]; then
  echo "❌ No python found. Activate your venv or install python3."
  exit 1
fi

# Load .env if present (so SUPABASE_URL/KEY are available)
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

# Check required env vars
if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_KEY" ]; then
  echo "❌ SUPABASE_URL and SUPABASE_KEY must be set."
  echo "   Copy .env.example to .env and fill in your keys."
  exit 1
fi

# Check playwright (only warn - plain HTTP sources still work)
if ! "$PYTHON" -c "import playwright" 2>/dev/null; then
  echo "⚠️  Playwright not installed. Browser-based sources (Indeed, Trakstar,"
  echo "   Foundit, Manipal, JobHai, DrLogy) will be skipped."
  echo "   To enable all sources, run:"
  echo "     pip install playwright && python -m playwright install chromium"
  echo
fi

echo "🚀 Running MediBrick lead scrapers..."
echo "   Target city: ${1:-Bengaluru}"
echo

"$PYTHON" -m scrapers.run_all "$@"

echo
echo "✅ Done. Open https://medibrick-analytics.vercel.app/leads.html to see results."
