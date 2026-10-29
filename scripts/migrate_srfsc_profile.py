#!/usr/bin/env python3
"""Add SRFSC quiz and request-survey storage without resetting existing accounts or tables."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect, text
from __init__ import app, db


def migrate(engine):
    changed = False
    if not inspect(engine).has_table('users'):
        # A fresh checkout has no accounts yet. Create the account tables without
        # seeding passwords or running the destructive db_init.py reset workflow.
        from model.user import User, Section, UserSection
        from model.persona import Persona, UserPersona
        db.metadata.create_all(bind=engine, tables=[
            User.__table__, Section.__table__, UserSection.__table__,
            Persona.__table__, UserPersona.__table__,
        ])
        changed = True
    elif '_srfsc_profile' not in {column['name'] for column in inspect(engine).get_columns('users')}:
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE users ADD COLUMN _srfsc_profile JSON NULL'))
        changed = True
    if not inspect(engine).has_table('srfsc_requests'):
        # Public council request survey (api/srfsc_requests.py).
        from model.srfsc_request import SrfscRequest
        db.metadata.create_all(bind=engine, tables=[SrfscRequest.__table__])
        changed = True
    return changed


if __name__ == '__main__':
    with app.app_context():
        changed = migrate(db.engine)
        print('Created SRFSC account/profile storage.' if changed else 'SRFSC profile storage already exists.')
