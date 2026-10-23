#!/bin/bash
set -euo pipefail

# Always start this checkout, including when launched from another directory.
cd "$(dirname "$0")"

# Quick script to run the Flask backend

echo "🚀 Starting Flask Backend..."

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
    echo "✓ Virtual environment activated"
else
    echo "❌ Virtual environment not found! Run setup_and_run.sh first"
    exit 1
fi

# Run the Flask app
python scripts/migrate_srfsc_profile.py
echo "✓ Starting Flask on http://localhost:${FLASK_PORT:-8587}"
echo ""
exec python main.py

