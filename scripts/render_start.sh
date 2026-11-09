#!/bin/sh
# Render start command: create the database on first boot, add SRFSC tables, then serve.
# Render Docker Command: sh scripts/render_start.sh
set -e
cd "$(dirname "$0")/.."

# Without the MySQL settings the app uses SQLite in instance/volumes/. Render's filesystem is wiped
# on every deploy and restart unless a persistent disk is mounted there, so say so in the logs.
if [ -z "${DB_ENDPOINT:-}" ] || [ -z "${DB_USERNAME:-}" ] || [ -z "${DB_PASSWORD:-}" ]; then
    echo "WARNING: DB_ENDPOINT/DB_USERNAME/DB_PASSWORD not set; using SQLite in instance/volumes/." >&2
    echo "WARNING: accounts, events and reports are lost on redeploy unless a Render disk is mounted there." >&2
fi

if [ ! -f instance/volumes/user_management.db ]; then
    ALLOW_NO_BACKUP=true python scripts/db_init.py
fi
python scripts/migrate_srfsc_profile.py
exec gunicorn main:app
