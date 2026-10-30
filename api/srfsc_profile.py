"""Community member signup plus a private, current-user-only profile API; reuses existing JWT login."""
import re
from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from __init__ import db
from api.authorize import token_required
from model.srfsc_interests import QUESTIONS, QUIZ_VERSION, recommendations, validate_answers
from model.user import User

srfsc_profile_api = Blueprint('srfsc_profile_api', __name__, url_prefix='/api/srfsc')

UID_PATTERN = re.compile(r'^[A-Za-z0-9._-]{3,40}$')
MIN_PASSWORD_LENGTH = 12


def profile_response(user):
    saved = user._srfsc_profile or {}
    answers = saved.get('answers', {})
    return {
        'name': user.name, 'uid': user.uid, 'account_role': user.role,
        'participation_role': answers.get('participation', 'resident'),
        'quiz_version': saved.get('quiz_version', QUIZ_VERSION),
        'answers': answers, 'completed': bool(answers),
        'updated_at': saved.get('updated_at'),
        'recommendations': recommendations(answers),
    }


def validate_signup(body):
    """Check a signup form. Returns (clean fields, error message); exactly one is None."""
    if not isinstance(body, dict):
        return None, 'Send your name, username, and password as JSON.'
    name, uid, password = body.get('name'), body.get('uid'), body.get('password')
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 100:
        return None, 'Name must contain between 2 and 100 characters.'
    if not isinstance(uid, str) or not UID_PATTERN.match(uid.strip()):
        return None, 'Username must be 3-40 letters, numbers, dots, dashes, or underscores.'
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return None, f'Password must contain at least {MIN_PASSWORD_LENGTH} characters.'
    if len(password) > 200:
        return None, 'Password must contain at most 200 characters.'
    return {'name': name.strip(), 'uid': uid.strip(), 'password': password}, None


@srfsc_profile_api.post('/signup')
def signup():
    """Public signup for neighbors. Always creates a regular member; council Admins are made by an operator."""
    clean, error = validate_signup(request.get_json(silent=True))
    if error:
        return jsonify(message=error), 400
    if User.query.filter(db.func.lower(User._uid) == clean['uid'].lower()).first():
        return jsonify(message='That username is taken. Please choose another.'), 409
    user = User(name=clean['name'], uid=clean['uid'], password=clean['password'], role='User')
    user.active = True
    user.last_verified = datetime.now(timezone.utc)
    db.session.add(user)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Could not create SRFSC member %s', clean['uid'])
        return jsonify(message='Your account could not be created. Please try again.'), 500
    return jsonify(message=f'Welcome, {user.name}! Your account is ready.', uid=user.uid), 201


@srfsc_profile_api.get('/quiz')
def quiz():
    return jsonify({'version': QUIZ_VERSION, 'questions': QUESTIONS})


@srfsc_profile_api.get('/profile')
@token_required()
def get_profile():
    return jsonify(profile_response(g.current_user))


@srfsc_profile_api.put('/profile')
@token_required()
def save_profile():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {'name', 'answers'}:
        return jsonify(message='Provide only name and answers. Account roles cannot be changed here.'), 400
    name = body['name']
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 100:
        return jsonify(message='Name must contain between 2 and 100 characters.'), 400
    try:
        answers = validate_answers(body['answers'])
    except ValueError as error:
        return jsonify(message=str(error)), 400

    user = g.current_user
    user.name = name.strip()
    user._srfsc_profile = {
        'quiz_version': QUIZ_VERSION, 'answers': answers,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Could not save SRFSC profile for user %s', user.id)
        return jsonify(message='Your profile could not be saved. Please try again.'), 500
    return jsonify(profile_response(user))
