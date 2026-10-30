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
from api.login import login_api
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
        self.app.register_blueprint(login_api)
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

    def test_login_resource_follows_jokes_style_get_and_post(self):
        status = self.client.get('/api/login')
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json['service'], 'srfsc-accounts')
        self.assertTrue(status.json['accounts_available'])
        self.assertNotIn('uid', status.json)
        self.assertEqual(self.client.get('/api/login/me').status_code, 401)
        login = self.client.post('/api/login', json={'uid': 'neighbor', 'password': 'TestPassword123!'})
        self.assertEqual(login.status_code, 200)
        self.assertIn('test_jwt=', login.headers['Set-Cookie'])
        self.assertEqual(self.client.get('/api/login/me').json['uid'], 'neighbor')
        self.assertEqual(self.client.get('/api/srfsc/profile').status_code, 200)
        self.assertEqual(self.client.delete('/api/login').status_code, 200)
        self.assertEqual(self.client.get('/api/login/me').status_code, 401)

    def test_login_rejects_bad_credentials_and_invalid_payloads(self):
        self.assertEqual(self.client.post('/api/login', json={
            'uid': 'neighbor', 'password': 'wrong-password'}).status_code, 401)
        for body in ([], 'invalid', None, {}, {'uid': 4, 'password': 'pass'},
                     {'uid': 'neighbor', 'password': []}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/api/login', json=body).status_code, 400)

    def test_login_readiness_explains_when_no_verified_accounts_exist(self):
        for user in User.query.all():
            user.active = False
        db.session.commit()
        response = self.client.get('/api/login')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json['accounts_available'])

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
        response = self.login('pending')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('Set-Cookie', response.headers)
        self.assertEqual(self.client.get('/api/srfsc/profile').status_code, 401)
        self.assertEqual(self.save().status_code, 401)

    def test_production_cookie_works_on_a_custom_backend_host_and_logout_clears_it(self):
        self.app.config.update(JWT_COOKIE_SECURE=True, JWT_COOKIE_SAMESITE='None')
        base = 'https://accounts.srfsc.example'
        response = self.client.post('/api/login', base_url=base, json={
            'uid': 'neighbor', 'password': 'TestPassword123!'})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers['Set-Cookie']
        for attribute in ('Secure', 'HttpOnly', 'SameSite=None', 'Path=/'):
            self.assertIn(attribute, cookie)
        self.assertNotIn('Domain=', cookie)
        self.assertEqual(self.client.get('/api/login/me', base_url=base).status_code, 200)
        response = self.client.delete('/api/login', base_url=base)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Max-Age=0', response.headers['Set-Cookie'])
        self.assertEqual(self.client.get('/api/login/me', base_url=base).status_code, 401)

    def test_logout_clears_a_flask_login_session_as_well_as_jwt(self):
        from flask_login import login_user
        user = User.query.filter_by(_uid='neighbor').first()
        login_manager.user_loader(lambda user_id: db.session.get(User, user.id))
        with self.app.test_request_context():
            login_user(user)
            from flask import session
            saved_session = dict(session)
        with self.client.session_transaction() as session:
            session.update(saved_session)
        self.assertEqual(self.client.get('/api/login/me').status_code, 200)
        self.assertEqual(self.client.delete('/api/login').status_code, 200)
        with self.client.session_transaction() as session:
            self.assertNotIn('_user_id', session)

    def test_explicit_shared_cookie_domain_is_used_for_login_and_logout(self):
        self.app.config['JWT_COOKIE_DOMAIN'] = 'srfsc.example'
        base = 'http://accounts.srfsc.example'
        response = self.client.post('/api/login', base_url=base, json={
            'uid': 'neighbor', 'password': 'TestPassword123!'})
        self.assertIn('Domain=srfsc.example', response.headers['Set-Cookie'])
        response = self.client.delete('/api/login', base_url=base)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Domain=srfsc.example', response.headers['Set-Cookie'])


    def test_signup_creates_an_active_member_who_can_log_in(self):
        response = self.client.post('/api/srfsc/signup', json={
            'name': ' New Neighbor ', 'uid': 'new.neighbor', 'password': 'LongEnoughPass1!', 'role': 'Admin'})
        self.assertEqual(response.status_code, 201)
        user = User.query.filter_by(_uid='new.neighbor').one()
        self.assertEqual((user.name, user.role, user.active), ('New Neighbor', 'User', True))
        login = self.client.post('/api/login', json={'uid': 'new.neighbor', 'password': 'LongEnoughPass1!'})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(self.client.get('/api/srfsc/profile').json['account_role'], 'User')

    def test_signup_rejects_taken_usernames_and_weak_input(self):
        cases = [
            ({'name': 'Someone', 'uid': 'NEIGHBOR', 'password': 'LongEnoughPass1!'}, 409),
            ({'name': 'Someone', 'uid': 'fresh', 'password': 'short'}, 400),
            ({'name': 'Someone', 'uid': 'has spaces', 'password': 'LongEnoughPass1!'}, 400),
            ({'name': 'S', 'uid': 'fresh', 'password': 'LongEnoughPass1!'}, 400),
            ([], 400),
        ]
        for body, status in cases:
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/api/srfsc/signup', json=body).status_code, status)
        self.assertIsNone(User.query.filter_by(_uid='fresh').first())

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


class FrontendCorsTest(unittest.TestCase):
    def test_real_app_allows_capstone_pages_preflight_and_error_responses(self):
        from main import app
        client = app.test_client()
        origin = 'https://srfsc-organization.github.io'
        for path, method in [('/api/login', 'POST'), ('/api/login', 'DELETE'),
                             ('/api/srfsc/profile', 'PUT')]:
            response = client.options(path, headers={
                'Origin': origin, 'Access-Control-Request-Method': method,
                'Access-Control-Request-Headers': 'content-type,x-origin'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], origin)
            self.assertEqual(response.headers['Access-Control-Allow-Credentials'], 'true')
            self.assertIn(method, response.headers['Access-Control-Allow-Methods'])
            self.assertIn('content-type', response.headers['Access-Control-Allow-Headers'].lower())
            self.assertIn('x-origin', response.headers['Access-Control-Allow-Headers'].lower())
        response = client.get('/api/login/me', headers={'Origin': origin})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers['Access-Control-Allow-Origin'], origin)
        response = client.options('/api/login', headers={
            'Origin': 'https://untrusted.example', 'Access-Control-Request-Method': 'POST'})
        self.assertNotIn('Access-Control-Allow-Origin', response.headers)


class MigrationTest(unittest.TestCase):
    def test_empty_request_table_gets_one_demo_request(self):
        engine = create_engine('sqlite://')
        migrate(engine)
        migrate(engine)
        with engine.connect() as connection:
            rows = connection.execute(text('SELECT name, status, created_at FROM srfsc_requests')).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:2], ('Demo Neighbor', 'New'))
        self.assertIsNotNone(rows[0][2])
        engine.dispose()

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
