"""Account/quiz integration tests using an isolated in-memory database."""
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from __init__ import db, login_manager
from api.user import user_api
from api.srfsc_profile import srfsc_profile_api
from model.user import User
from model.persona import Persona  # Register the user/persona relationship.
from scripts.migrate_srfsc_profile import migrate

ANSWERS = {'participation': 'volunteer', 'interests': ['home', 'events'],
           'experience': 'new', 'availability': 'weekends', 'goal': 'help'}


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='srfsc-tests-only-secret-at-least-32-characters',
                               SQLALCHEMY_DATABASE_URI='sqlite://',
                               JWT_TOKEN_NAME='test_jwt', JWT_TOKEN_MAX_AGE=600)
        db.init_app(self.app)
        login_manager.init_app(self.app)
        login_manager.user_loader(lambda user_id: None)
        self.app.register_blueprint(user_api)
        self.app.register_blueprint(srfsc_profile_api)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        for uid, active in [('neighbor', True), ('other', True), ('pending', False)]:
            user = User(name=uid, uid=uid, password='TestPassword123!')
            user.active = active
            db.session.add(user)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def login(self, uid='neighbor', client=None):
        return (client or self.client).post('/api/authenticate', json={
            'uid': uid, 'password': 'TestPassword123!'})

    def save(self, **overrides):
        return self.client.put('/api/srfsc/profile', json={
            'name': 'Neighborhood Member', 'answers': ANSWERS, **overrides})

    def test_quiz_is_public_and_profile_requires_login(self):
        self.assertEqual(len(self.client.get('/api/srfsc/quiz').json['questions']), 5)
        self.assertEqual(self.client.get('/api/srfsc/profile').status_code, 401)
        self.assertEqual(self.save().status_code, 401)

    def test_saved_answers_survive_login_and_are_private(self):
        self.assertEqual(self.login().status_code, 200)
        self.assertFalse(self.client.get('/api/srfsc/profile').json['completed'])
        saved = self.save()
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json['answers'], ANSWERS)
        self.assertEqual(saved.json['account_role'], 'User')
        self.assertEqual(saved.json['recommendations'][0]['path'], 'get-involved/')
        self.assertNotIn('srfsc_profile', self.client.get('/api/id').json)
        second = self.app.test_client()
        self.login(client=second)
        self.assertEqual(second.get('/api/srfsc/profile').json['answers'], ANSWERS)
        self.login('other', second)
        self.assertFalse(second.get('/api/srfsc/profile').json['completed'])

    def test_update_replaces_prior_answers(self):
        self.login()
        self.save()
        answers = {**ANSWERS, 'participation': 'resident', 'interests': ['home'], 'goal': 'prepare'}
        saved = self.save(answers=answers)
        self.assertEqual(saved.json['participation_role'], 'resident')
        self.assertEqual(saved.json['recommendations'], [{'title': 'Preparation resources', 'path': 'prepare/'}])

    def test_invalid_answers_do_not_modify_profile(self):
        self.login()
        self.save()
        for answers in (None, [], {}, {**ANSWERS, 'interests': []},
                        {**ANSWERS, 'participation': 'Admin'},
                        {**ANSWERS, 'interests': [{}]},
                        {**ANSWERS, 'goal': 'unknown'}):
            with self.subTest(answers=answers):
                self.assertEqual(self.save(answers=answers).status_code, 400)
        self.assertEqual(self.client.get('/api/srfsc/profile').json['answers'], ANSWERS)

    def test_role_and_target_user_cannot_be_submitted(self):
        self.login()
        for field, value in [('role', 'Admin'), ('uid', 'other'), ('user_id', 2)]:
            self.assertEqual(self.save(**{field: value}).status_code, 400)
        self.assertEqual(User.query.filter_by(_uid='neighbor').first().role, 'User')

    def test_pending_account_cannot_load_or_save_profile(self):
        self.login('pending')
        self.assertEqual(self.client.get('/api/srfsc/profile').status_code, 403)
        self.assertEqual(self.save().status_code, 403)

    def test_malformed_payload_and_name_validation(self):
        self.login()
        for body in ([], None, 'text'):
            self.assertEqual(self.client.put('/api/srfsc/profile', json=body).status_code, 400)
        for name in (' ', 'a', 'x' * 101, 42):
            self.assertEqual(self.save(name=name).status_code, 400)

    def test_database_error_rolls_back_name_and_answers(self):
        self.login()
        with patch.object(db.session, 'commit', side_effect=SQLAlchemyError('test failure')):
            self.assertEqual(self.save().status_code, 500)
        profile = self.client.get('/api/srfsc/profile').json
        self.assertEqual(profile['name'], 'neighbor')
        self.assertFalse(profile['completed'])

    def test_operator_can_provision_a_verified_member_without_default_passwords(self):
        from scripts.create_srfsc_member import main
        with patch('sys.argv', ['create_srfsc_member', '--uid', 'new-neighbor', '--name', 'New Neighbor']), \
                patch('scripts.create_srfsc_member.app', self.app), \
                patch('scripts.create_srfsc_member.getpass', side_effect=['LongTestPassword123!', 'LongTestPassword123!']), \
                redirect_stdout(StringIO()):
            main()
        self.assertEqual(self.client.post('/api/authenticate', json={
            'uid': 'new-neighbor', 'password': 'LongTestPassword123!'}).status_code, 200)
        profile = self.client.get('/api/srfsc/profile')
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json['account_role'], 'User')

    def test_admin_backup_round_trip_preserves_private_answers(self):
        from api.data_export_import_api import ExportAllData, ImportAllData
        self.login()
        self.save()
        exported = next(user for user in ExportAllData()._export_users() if user['uid'] == 'neighbor')
        exported['uid'] = 'restored-neighbor'
        imported = ImportAllData()._import_users([exported])
        self.assertEqual(imported['imported'], 1)
        restored = User.query.filter_by(_uid='restored-neighbor').first()
        self.assertEqual(restored._srfsc_profile['answers'], ANSWERS)


class MigrationTest(unittest.TestCase):
    def test_fresh_database_creates_account_storage_without_seeding_passwords(self):
        engine = create_engine('sqlite://')
        self.assertTrue(migrate(engine))
        self.assertFalse(migrate(engine))
        with engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT COUNT(*) FROM users')).scalar(), 0)
        engine.dispose()

    def test_migration_preserves_existing_rows_and_can_repeat(self):
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)'))
            connection.execute(text("INSERT INTO users VALUES (1, 'Existing Neighbor')"))
        self.assertTrue(migrate(engine))
        self.assertFalse(migrate(engine))
        with engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT name, _srfsc_profile FROM users')).one(),
                             ('Existing Neighbor', None))
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
