#!/usr/bin/env python3
"""Council operator CLI: provision a verified account without school/GitHub signup."""
import argparse
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from __init__ import app, db
from model.user import User
from model.persona import Persona  # Register relationships for the account model.
from scripts.migrate_srfsc_profile import migrate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uid', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--role', choices=['User', 'Admin'], default='User')
    args = parser.parse_args()
    if not 2 <= len(args.uid.strip()) <= 100 or not 2 <= len(args.name.strip()) <= 100:
        parser.error('User ID and name must contain 2–100 characters.')
    with app.app_context():
        migrate(db.engine)
        if User.query.filter_by(_uid=args.uid.strip()).first():
            parser.error('That user ID already exists; the account was not changed.')
        password = getpass('New password (at least 12 characters): ')
        if len(password) < 12:
            parser.error('Password must contain at least 12 characters.')
        if password != getpass('Confirm password: '):
            parser.error('Passwords did not match.')
        user = User(name=args.name.strip(), uid=args.uid.strip(), password=password, role=args.role)
        user.active = True
        user.last_verified = datetime.now(timezone.utc)
        db.session.add(user)
        db.session.commit()
        print(f'Created verified {args.role} account for {user.uid}.')


if __name__ == '__main__':
    main()
