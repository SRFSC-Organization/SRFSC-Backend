#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

# The same private signing key must survive restarts and be shared by workers.
: "${SECRET_KEY:?Set SECRET_KEY in the hosting environment before starting production}"
if [ "$SECRET_KEY" = 'SECRET_KEY' ]; then
    echo 'Replace the development SECRET_KEY before starting production.' >&2
    exit 1
fi
export IS_PRODUCTION=true

# Mount persistent storage at /app/instance for SQLite accounts and uploads.
mkdir -p instance/volumes
python scripts/migrate_srfsc_profile.py

exec gunicorn main:app --bind "0.0.0.0:${PORT:-8587}"
