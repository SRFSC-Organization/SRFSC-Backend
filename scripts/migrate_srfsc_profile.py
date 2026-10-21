#!/usr/bin/env python3
"""Add private quiz storage without resetting existing accounts or tables."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect, text
from __init__ import app, db


def migrate(engine):
    if not inspect(engine).has_table('users'):
        # A fresh checkout has no accounts yet. Create the account tables without
        # seeding passwords or running the destructive db_init.py reset workflow.
        from model.user import User, Section, UserSection
        from model.persona import Persona, UserPersona
        db.metadata.create_all(bind=engine, tables=[
            User.__table__, Section.__table__, UserSection.__table__,
            Persona.__table__, UserPersona.__table__,
        ])
        return True
    columns = {column['name'] for column in inspect(engine).get_columns('users')}
    if '_srfsc_profile' in columns:
        return False
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE users ADD COLUMN _srfsc_profile JSON NULL'))
    return True


if __name__ == '__main__':
    with app.app_context():
        changed = migrate(db.engine)
        print('Created SRFSC account/profile storage.' if changed else 'SRFSC profile storage already exists.')
