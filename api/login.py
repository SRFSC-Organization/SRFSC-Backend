"""Frontend login resource, registered like hacks/joke.py's JokesAPI."""
from flask import Blueprint, jsonify, request
from flask_restful import Api
from sqlalchemy.exc import SQLAlchemyError

from __init__ import db
from api.user import UserAPI
from model.user import User

login_api = Blueprint('login_api', __name__, url_prefix='/api/login')
api = Api(login_api)


class LoginAPI:
    class _Login(UserAPI._Security):
        """Reuse password verification, JWT cookies, and logout in the user API."""

        def get(self):
            try:
                # Verify the account schema as well as the server connection.
                account = db.session.execute(
                    db.select(User.id, User._srfsc_profile).where(User.active.is_(True)).limit(1)
                ).first()
            except SQLAlchemyError:
                db.session.rollback()
                return {'service': 'srfsc-accounts', 'message': 'Account storage is not ready. Please contact the council.'}, 503
            return jsonify(service='srfsc-accounts', status='ready', accounts_available=account is not None)

        def post(self):
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return {'message': 'Provide your user ID and password as JSON.'}, 400
            if not isinstance(body.get('uid'), str) or not body['uid'].strip():
                return {'message': 'User ID is required.'}, 400
            if not isinstance(body.get('password'), str) or not body['password']:
                return {'message': 'Password is required.'}, 400
            return super().post()

    api.add_resource(_Login, '', '/')
    api.add_resource(UserAPI._ID, '/me', '/me/')
