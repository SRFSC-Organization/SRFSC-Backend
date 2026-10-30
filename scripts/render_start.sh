#!/bin/sh
# Render start command: create the database on first boot, add SRFSC tables, then serve.
# Render Docker Command: sh scripts/render_start.sh
set -e
cd "$(dirname "$0")/.."

if [ ! -f instance/volumes/user_management.db ]; then
    ALLOW_NO_BACKUP=true python scripts/db_init.py
fi
python scripts/migrate_srfsc_profile.py
exec gunicorn main:app
