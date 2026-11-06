"""
SRFSC council API tests (events, volunteers, hazard reports, updates, fire weather, map),
moved from capstone2pages/srfsc_backend. Each test gets a fresh app and temporary SQLite file.
"""
import os
import tempfile
import unittest
from datetime import date, timedelta

import requests
from flask import Flask

from __init__ import db, login_manager
from api.user import user_api
from api.srfsc_conditions import ConditionsCache, rate_fire_weather
from api.srfsc_council import srfsc_council_api
from api.srfsc_council_validation import validate_event, validate_hazard_report, validate_volunteer
from model.user import User
from model.persona import Persona  # noqa: F401  Register the user/persona relationship.
from model.srfsc_council import SrfscEvent, SrfscUpdate, init_srfsc_council as init_srfsc

ADMIN_KEY = 'test-council-key'


class SrfscValidationTest(unittest.TestCase):

    def test_volunteer_requires_valid_email(self):
        _, error = validate_volunteer({"name": "Sam", "email": "nope", "interest": "Clearing Day"})
        self.assertIn("email", error)

    def test_volunteer_rejects_unknown_interest(self):
        _, error = validate_volunteer({"name": "Sam", "email": "s@x.org", "interest": "Arson"})
        self.assertIn("interest", error)

    def test_volunteer_trims_and_lowercases(self):
        clean, error = validate_volunteer({"name": "  Sam ", "email": "S@X.org", "interest": "Clearing Day"})
        self.assertIsNone(error)
        self.assertEqual(clean["name"], "Sam")
        self.assertEqual(clean["email"], "s@x.org")
        self.assertIsNone(clean["phone"])

    def test_report_requires_description(self):
        _, error = validate_hazard_report({"hazard_type": "Hazard Tree", "location": "Canyon"})
        self.assertIn("description", error)

    def test_event_date_must_be_iso(self):
        _, error = validate_event({"title": "T", "event_type": "Clearing Day",
                                   "event_date": "10/17/2026", "location": "Park"})
        self.assertIn("YYYY-MM-DD", error)

    def test_non_object_body_is_rejected(self):
        _, error = validate_volunteer(None)
        self.assertIn("JSON object", error)


class SrfscApiTest(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='srfsc-tests-only-secret-at-least-32-characters',
                               SQLALCHEMY_DATABASE_URI='sqlite:///' + os.path.join(self.tmp_dir.name, 'srfsc_test.db'),
                               JWT_TOKEN_NAME='test_jwt', JWT_TOKEN_MAX_AGE=600, SRFSC_ADMIN_KEY=ADMIN_KEY)
        db.init_app(self.app)
        login_manager.init_app(self.app)
        login_manager.user_loader(lambda user_id: None)
        self.app.register_blueprint(user_api)
        self.app.register_blueprint(srfsc_council_api)
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        # Empty tables for exact counts; tests that want starter content call init_srfsc().
        db.create_all()
        for uid, role in [('council', 'Admin'), ('neighbor', 'User')]:
            user = User(name=uid, uid=uid, password='TestPassword123!', role=role)
            user.active = True
            db.session.add(user)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()
        self.tmp_dir.cleanup()

    def admin_headers(self):
        return {"Authorization": f"Bearer {ADMIN_KEY}"}

    def test_wrong_admin_key_is_rejected(self):
        response = self.client.get('/api/srfsc/volunteers', headers={"Authorization": "Bearer nope"})
        self.assertEqual(response.status_code, 401)

    def test_admin_endpoints_fail_closed_without_configured_key(self):
        self.app.config['SRFSC_ADMIN_KEY'] = ''
        response = self.client.get('/api/srfsc/volunteers', headers={"Authorization": "Bearer "})
        self.assertEqual(response.status_code, 401)

    def test_real_app_gives_the_team_site_credentialed_cors_for_council_calls(self):
        from main import app
        client = app.test_client()
        origin = 'https://srfsc-organization.github.io'
        # The Council admin page sends the council key in an Authorization header.
        for path, method in [('/api/srfsc/map', 'GET'), ('/api/srfsc/reports', 'POST'), ('/api/srfsc/volunteers', 'GET')]:
            response = client.options(path, headers={
                'Origin': origin, 'Access-Control-Request-Method': method,
                'Access-Control-Request-Headers': 'content-type,x-origin,authorization'})
            self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), origin)
            self.assertEqual(response.headers.get('Access-Control-Allow-Credentials'), 'true')
            self.assertIn('authorization', response.headers['Access-Control-Allow-Headers'].lower())
        unknown = client.options('/api/srfsc/map', headers={
            'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'GET'})
        self.assertIsNone(unknown.headers.get('Access-Control-Allow-Origin'))

    def test_public_volunteer_signup_hides_contact_info(self):
        response = self.client.post('/api/srfsc/volunteers', json={
            "name": "Sam", "email": "sam@example.org", "interest": "Clearing Day"})
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("email", response.get_json())

    def test_invalid_volunteer_signup_returns_400(self):
        response = self.client.post('/api/srfsc/volunteers', json={"name": "Sam"})
        self.assertEqual(response.status_code, 400)

    def test_volunteer_list_requires_auth(self):
        self.assertEqual(self.client.get('/api/srfsc/volunteers').status_code, 401)

    def test_admin_can_list_volunteers(self):
        self.client.post('/api/srfsc/volunteers', json={
            "name": "Sam", "email": "sam@example.org", "interest": "Board Leadership"})
        response = self.client.get('/api/srfsc/volunteers', headers=self.admin_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()[0]["email"], "sam@example.org")

    def test_hazard_report_lifecycle(self):
        created = self.client.post('/api/srfsc/reports', json={
            "hazard_type": "Hazard Tree", "location": "Aviary Dr canyon",
            "description": "Dead eucalyptus leaning over trail", "reporter_email": "r@example.org"})
        self.assertEqual(created.status_code, 201)
        body = created.get_json()
        self.assertEqual(body["status"], "New")
        self.assertNotIn("reporter_email", body)

        updated = self.client.put(f'/api/srfsc/reports/{body["id"]}', json={"status": "Resolved"},
                                  headers=self.admin_headers())
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["status"], "Resolved")

        stats = self.client.get('/api/srfsc/stats').get_json()
        self.assertEqual(stats["hazard_reports"], 1)
        self.assertEqual(stats["resolved_reports"], 1)

    def test_events_hide_past_by_default(self):
        SrfscEvent("Past", "Clearing Day", date.today() - timedelta(days=1), "Park").create()
        SrfscEvent("Next", "Clearing Day", date.today() + timedelta(days=1), "Park").create()
        titles = [e["title"] for e in self.client.get('/api/srfsc/events').get_json()]
        self.assertEqual(titles, ["Next"])
        all_titles = [e["title"] for e in self.client.get('/api/srfsc/events?all=true').get_json()]
        self.assertEqual(len(all_titles), 2)

    def test_event_create_requires_admin(self):
        payload = {"title": "Expo", "event_type": "Fire Safety Expo",
                   "event_date": "2030-01-01", "location": "Park"}
        self.assertEqual(self.client.post('/api/srfsc/events', json=payload).status_code, 401)
        created = self.client.post('/api/srfsc/events', json=payload, headers=self.admin_headers())
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["event_date"], "2030-01-01")

    def test_seed_is_idempotent(self):
        init_srfsc()
        first_count = SrfscEvent.query.count()
        init_srfsc()
        self.assertEqual(SrfscEvent.query.count(), first_count)
        self.assertGreater(first_count, 0)

    def test_rsvp_counts_once_per_email(self):
        event = SrfscEvent("Clearing Day", "Clearing Day", date.today() + timedelta(days=3), "Canyon").create()
        rsvp = {"name": "Sam", "email": "sam@example.org"}
        first = self.client.post(f'/api/srfsc/events/{event.id}/rsvp', json=rsvp)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.get_json()["rsvp_count"], 1)
        duplicate = self.client.post(f'/api/srfsc/events/{event.id}/rsvp', json={**rsvp, "email": "SAM@example.org"})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.client.get('/api/srfsc/events').get_json()[0]["rsvp_count"], 1)

    def test_rsvp_rejects_past_and_missing_events(self):
        past = SrfscEvent("Old", "Clearing Day", date.today() - timedelta(days=3), "Canyon").create()
        rsvp = {"name": "Sam", "email": "sam@example.org"}
        self.assertEqual(self.client.post(f'/api/srfsc/events/{past.id}/rsvp', json=rsvp).status_code, 400)
        self.assertEqual(self.client.post('/api/srfsc/events/999/rsvp', json=rsvp).status_code, 404)

    def test_updates_are_public_but_posting_requires_admin(self):
        payload = {"title": "Chipper day recap", "body": "40 bags of brush removed.", "category": "News"}
        self.assertEqual(self.client.post('/api/srfsc/updates', json=payload).status_code, 401)
        created = self.client.post('/api/srfsc/updates', json=payload, headers=self.admin_headers())
        self.assertEqual(created.status_code, 201)
        feed = self.client.get('/api/srfsc/updates?limit=1').get_json()
        self.assertEqual([item["title"] for item in feed], ["Chipper day recap"])

    def test_stats_include_next_event_and_open_reports(self):
        SrfscEvent("Later", "Clearing Day", date.today() + timedelta(days=9), "Park").create()
        SrfscEvent("Sooner", "Block Meeting", date.today() + timedelta(days=2), "Library").create()
        self.client.post('/api/srfsc/reports', json={
            "hazard_type": "Other", "location": "Trail", "description": "Brush pile"})
        stats = self.client.get('/api/srfsc/stats').get_json()
        self.assertEqual(stats["next_event"]["title"], "Sooner")
        self.assertEqual(stats["open_reports"], 1)
        self.assertEqual(stats["upcoming_events"], 2)

    def test_seed_creates_updates_even_when_events_exist(self):
        SrfscEvent("Existing", "Clearing Day", date.today(), "Park").create()
        init_srfsc()
        self.assertGreater(SrfscUpdate.query.count(), 0)


class SrfscCouncilAdminTest(unittest.TestCase):
    """Admin endpoints accept the council key or a signed-in Admin account, nothing else."""

    setUp = SrfscApiTest.setUp
    tearDown = SrfscApiTest.tearDown

    def login(self, uid):
        self.assertEqual(self.client.post('/api/authenticate', json={
            'uid': uid, 'password': 'TestPassword123!'}).status_code, 200)

    def test_admin_account_can_manage_without_the_key(self):
        self.assertEqual(self.client.get('/api/srfsc/volunteers').status_code, 401)
        self.login('neighbor')
        self.assertEqual(self.client.get('/api/srfsc/volunteers').status_code, 403)
        self.login('council')
        self.assertEqual(self.client.get('/api/srfsc/volunteers').status_code, 200)
        created = self.client.post('/api/srfsc/events', json={
            "title": "Admin event", "event_type": "Clearing Day", "event_date": "2030-01-01", "location": "Park"})
        self.assertEqual(created.status_code, 201)

    def test_wrong_key_without_a_session_is_refused(self):
        response = self.client.get('/api/srfsc/volunteers', headers={"Authorization": "Bearer not-the-key"})
        self.assertEqual(response.status_code, 401)

    def test_unset_key_never_matches(self):
        self.app.config['SRFSC_ADMIN_KEY'] = ''
        response = self.client.get('/api/srfsc/volunteers', headers={"Authorization": "Bearer "})
        self.assertEqual(response.status_code, 401)


class SrfscMapTest(unittest.TestCase):
    """The public map: pins for upcoming events and open hazard reports."""

    setUp = SrfscApiTest.setUp
    tearDown = SrfscApiTest.tearDown
    admin_headers = SrfscApiTest.admin_headers

    REPORT = {"hazard_type": "Hazard Tree", "location": "Canyon behind Aviary Dr",
              "description": "Dead pine leaning over the trail.", "reporter_email": "pat@example.org"}

    def test_pins_must_be_complete_numbers_inside_scripps_ranch(self):
        for point, message in [({"latitude": 32.91}, "both"),
                               ({"latitude": "32.91", "longitude": -117.09}, "numbers"),
                               ({"latitude": True, "longitude": -117.09}, "numbers"),
                               ({"latitude": 34.05, "longitude": -118.24}, "inside Scripps Ranch")]:
            with self.subTest(point=point):
                response = self.client.post('/api/srfsc/reports', json={**self.REPORT, **point})
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.get_json()["message"])
        self.assertEqual(self.client.post('/api/srfsc/reports', json=self.REPORT).status_code, 201)

    def test_map_shows_upcoming_events_and_open_hazards_without_private_details(self):
        soon = (date.today() + timedelta(days=3)).isoformat()
        past = (date.today() - timedelta(days=3)).isoformat()
        for day in (soon, past):
            self.client.post('/api/srfsc/events', headers=self.admin_headers(), json={
                "title": f"Clearing {day}", "event_type": "Clearing Day", "event_date": day,
                "location": "Lake Miramar", "latitude": 32.9119, "longitude": -117.0950})
        open_id = self.client.post('/api/srfsc/reports', json={
            **self.REPORT, "latitude": 32.912345, "longitude": -117.098765}).get_json()["id"]
        done_id = self.client.post('/api/srfsc/reports', json={
            **self.REPORT, "latitude": 32.92, "longitude": -117.09}).get_json()["id"]
        self.client.post('/api/srfsc/reports', json=self.REPORT)   # no pin: not on the map
        self.client.put(f'/api/srfsc/reports/{done_id}', json={"status": "Resolved"}, headers=self.admin_headers())

        pins = self.client.get('/api/srfsc/map').get_json()
        self.assertEqual([event["event_date"] for event in pins["events"]], [soon])
        self.assertEqual([hazard["id"] for hazard in pins["hazards"]], [open_id])
        hazard = pins["hazards"][0]
        self.assertEqual((hazard["latitude"], hazard["longitude"]), (32.912, -117.099))
        self.assertNotIn("description", hazard)
        self.assertNotIn("reporter_email", hazard)

    def test_older_database_gets_map_columns_and_starter_positions(self):
        db.drop_all()
        with db.engine.begin() as connection:
            connection.execute(db.text(
                "CREATE TABLE srfsc_events (id INTEGER PRIMARY KEY, title VARCHAR(255) NOT NULL, "
                "event_type VARCHAR(64) NOT NULL, event_date DATE NOT NULL, location VARCHAR(255) NOT NULL, "
                "description TEXT, created_at DATETIME)"))
            connection.execute(db.text(
                "INSERT INTO srfsc_events (title, event_type, event_date, location) "
                "VALUES ('Old expo', 'Fire Safety Expo', '2030-01-01', 'Scripps Ranch Community Park')"))
        init_srfsc()
        init_srfsc()   # running again changes nothing
        event = SrfscEvent.query.filter_by(title='Old expo').one()
        self.assertEqual((event.latitude, event.longitude), (32.9258, -117.0718))


class SrfscConditionsTest(unittest.TestCase):

    def test_red_flag_alert_is_extreme_regardless_of_weather(self):
        level, _ = rate_fire_weather(80, 0, ["Red Flag Warning"])
        self.assertEqual(level, "Extreme")

    def test_dry_and_windy_is_high(self):
        self.assertEqual(rate_fire_weather(10, 30, [])[0], "High")

    def test_dry_or_breezy_is_elevated(self):
        self.assertEqual(rate_fire_weather(24, 5, [])[0], "Elevated")
        self.assertEqual(rate_fire_weather(60, 18, [])[0], "Elevated")

    def test_calm_and_humid_is_normal(self):
        self.assertEqual(rate_fire_weather(60, 5, ["Extreme Heat Warning"])[0], "Normal")

    def test_missing_weather_is_unknown(self):
        self.assertEqual(rate_fire_weather(None, None, [])[0], "Unknown")

    def test_cache_reuses_payload_within_ttl(self):
        calls = []
        now = [0]
        cache = ConditionsCache(fetcher=lambda: calls.append(1) or {"level": "Normal"},
                                clock=lambda: now[0], ttl_seconds=600)
        cache.get()
        now[0] = 599
        cache.get()
        self.assertEqual(len(calls), 1)
        now[0] = 601
        cache.get()
        self.assertEqual(len(calls), 2)

    def test_cache_serves_stale_payload_when_nws_fails(self):
        responses = [{"level": "Elevated"}]

        def flaky_fetch():
            if responses:
                return responses.pop()
            raise requests.ConnectionError("NWS down")

        now = [0]
        cache = ConditionsCache(fetcher=flaky_fetch, clock=lambda: now[0], ttl_seconds=10)
        cache.get()
        now[0] = 20
        stale = cache.get()
        self.assertEqual(stale["level"], "Elevated")
        self.assertTrue(stale["stale"])

    def test_cache_reports_unknown_when_nws_never_answers(self):
        def failing_fetch():
            raise requests.Timeout("slow")
        self.assertEqual(ConditionsCache(fetcher=failing_fetch).get()["level"], "Unknown")


if __name__ == '__main__':
    unittest.main()
