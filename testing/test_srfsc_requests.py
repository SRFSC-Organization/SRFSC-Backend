"""Council request survey tests using an isolated in-memory database."""
import io
import unittest

from flask import Flask
from PIL import Image

from __init__ import db, login_manager
from api.user import user_api
from api.srfsc_requests import srfsc_requests_api, validate_request
from model.user import User
from model.persona import Persona  # Register the user/persona relationship.
from model.srfsc_request import SrfscRequest

SURVEY = {'name': 'Pat Neighbor', 'email': 'Pat@Example.org', 'request_type': 'Home wildfire assessment',
          'message': 'Please check the brush behind our house on Ironwood Rd.', 'neighborhood': 'Ironwood'}


def photo(fmt='PNG', size=(2400, 1200), gps=False):
    """An in-memory image upload; gps=True adds EXIF location data like a phone camera."""
    image = Image.new('RGB', size, (200, 90, 40))
    buffer = io.BytesIO()
    if gps:
        exif = Image.Exif()
        exif[0x8825] = {1: 'N', 2: (32.0, 54.0, 0.0)}   # GPSInfo
        image.save(buffer, fmt, exif=exif)
    else:
        image.save(buffer, fmt)
    buffer.seek(0)
    return buffer, f'photo.{fmt.lower()}'


class RequestSurveyTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='srfsc-tests-only-secret-at-least-32-characters',
                               SQLALCHEMY_DATABASE_URI='sqlite://',
                               JWT_TOKEN_NAME='test_jwt', JWT_TOKEN_MAX_AGE=600)
        db.init_app(self.app)
        login_manager.init_app(self.app)
        login_manager.user_loader(lambda user_id: None)
        self.app.register_blueprint(user_api)
        self.app.register_blueprint(srfsc_requests_api)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        for uid, role in [('council', 'Admin'), ('neighbor', 'User')]:
            user = User(name=uid, uid=uid, password='TestPassword123!', role=role)
            user.active = True
            db.session.add(user)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def login(self, uid):
        self.assertEqual(self.client.post('/api/authenticate', json={
            'uid': uid, 'password': 'TestPassword123!'}).status_code, 200)

    def test_public_can_submit_without_login(self):
        response = self.client.post('/api/srfsc/requests', json=SURVEY)
        self.assertEqual(response.status_code, 201)
        self.assertIn('Request #1', response.json['message'])
        saved = db.session.get(SrfscRequest, response.json['id'])
        self.assertEqual(saved.email, 'pat@example.org')
        self.assertEqual(saved.status, 'New')

    def test_invalid_surveys_are_rejected(self):
        for body, field in [({**SURVEY, 'email': 'not-an-email'}, 'email'),
                            ({**SURVEY, 'request_type': 'Free pizza'}, 'Request type'),
                            ({**SURVEY, 'message': 'short'}, 'Message'),
                            ({key: value for key, value in SURVEY.items() if key != 'name'}, 'Name')]:
            with self.subTest(field=field):
                response = self.client.post('/api/srfsc/requests', json=body)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json['message'])
        self.assertEqual(SrfscRequest.query.count(), 0)

    def test_validate_request_returns_first_error(self):
        clean, error = validate_request({**SURVEY, 'name': '', 'email': 'bad'})
        self.assertIsNone(clean)
        self.assertEqual(error, 'Name is required.')

    def test_only_admins_can_list_requests(self):
        self.client.post('/api/srfsc/requests', json=SURVEY)
        self.assertEqual(self.client.get('/api/srfsc/requests').status_code, 401)
        self.login('neighbor')
        self.assertEqual(self.client.get('/api/srfsc/requests').status_code, 403)
        self.login('council')
        listed = self.client.get('/api/srfsc/requests')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json[0]['email'], 'pat@example.org')

    def test_admin_review_updates_status_and_note(self):
        request_id = self.client.post('/api/srfsc/requests', json=SURVEY).json['id']
        self.login('council')
        reviewed = self.client.put(f'/api/srfsc/requests/{request_id}',
                                   json={'status': 'In review', 'admin_note': 'Visit scheduled for Saturday.'})
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reviewed.json['status'], 'In review')
        self.assertEqual(reviewed.json['reviewed_by'], 'council')
        self.assertEqual(len(self.client.get('/api/srfsc/requests?status=In review').json), 1)
        self.assertEqual(len(self.client.get('/api/srfsc/requests?status=Resolved').json), 0)

    def test_review_rejects_bad_input_and_non_admins(self):
        request_id = self.client.post('/api/srfsc/requests', json=SURVEY).json['id']
        self.login('council')
        for body in ({'status': 'Done'}, {}, {'status': 'Resolved', 'email': 'x@y.z'}):
            with self.subTest(body=body):
                self.assertEqual(self.client.put(f'/api/srfsc/requests/{request_id}', json=body).status_code, 400)
        self.assertEqual(self.client.put('/api/srfsc/requests/999', json={'status': 'Resolved'}).status_code, 404)
        self.login('neighbor')
        self.assertEqual(self.client.put(f'/api/srfsc/requests/{request_id}',
                                         json={'status': 'Resolved'}).status_code, 403)
        self.assertEqual(db.session.get(SrfscRequest, request_id).status, 'New')


    def submit_with_photos(self, *photos):
        return self.client.post('/api/srfsc/requests', data={**SURVEY, 'photos': list(photos)},
                                content_type='multipart/form-data')

    def test_photos_are_saved_resized_and_stripped_of_location(self):
        response = self.submit_with_photos(photo(), photo('JPEG', (800, 600), gps=True))
        self.assertEqual(response.status_code, 201)
        self.assertIn('with 2 photos', response.json['message'])
        self.login('council')
        detail = self.client.get(f"/api/srfsc/requests/{response.json['id']}").json
        self.assertEqual(detail['photo_count'], 2)
        self.assertEqual(len(detail['photo_ids']), 2)
        wide, phone = (self.client.get(f"/api/srfsc/requests/{detail['id']}/photos/{pid}") for pid in detail['photo_ids'])
        self.assertEqual(wide.mimetype, 'image/jpeg')
        self.assertIn('private', wide.headers['Cache-Control'])
        self.assertEqual(Image.open(io.BytesIO(wide.data)).size, (1600, 800))
        self.assertNotIn(0x8825, Image.open(io.BytesIO(phone.data)).getexif())

    def test_photo_limits_and_bad_files_are_refused(self):
        too_many = self.submit_with_photos(photo(), photo(), photo(), photo())
        self.assertEqual(too_many.status_code, 400)
        self.assertIn('at most 3', too_many.json['message'])
        not_image = self.submit_with_photos((io.BytesIO(b'not really a picture'), 'notes.png'))
        self.assertEqual(not_image.status_code, 400)
        self.assertEqual(SrfscRequest.query.count(), 0)
        # Survey fields are still checked when photos come along.
        bad = self.client.post('/api/srfsc/requests', data={**SURVEY, 'email': 'nope', 'photos': [photo()]},
                               content_type='multipart/form-data')
        self.assertEqual(bad.status_code, 400)

    def test_only_admins_can_open_a_request_or_its_photos(self):
        request_id = self.submit_with_photos(photo()).json['id']
        detail_url = f'/api/srfsc/requests/{request_id}'
        self.assertEqual(self.client.get(detail_url).status_code, 401)
        self.login('neighbor')
        self.assertEqual(self.client.get(detail_url).status_code, 403)
        self.login('council')
        photo_id = self.client.get(detail_url).json['photo_ids'][0]
        self.assertEqual(self.client.get(f'{detail_url}/photos/{photo_id}').status_code, 200)
        # A photo only opens under the request it belongs to.
        other = self.client.post('/api/srfsc/requests', json=SURVEY).json['id']
        self.assertEqual(self.client.get(f'/api/srfsc/requests/{other}/photos/{photo_id}').status_code, 404)
        self.assertEqual(self.client.get('/api/srfsc/requests/999').status_code, 404)
        self.login('neighbor')
        self.assertEqual(self.client.get(f'{detail_url}/photos/{photo_id}').status_code, 403)

if __name__ == '__main__':
    unittest.main()
