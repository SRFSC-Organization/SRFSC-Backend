"""Private, current-user-only community profile API; reuses existing JWT login."""
from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from __init__ import db
from api.authorize import token_required
from model.srfsc_interests import QUESTIONS, QUIZ_VERSION, recommendations, validate_answers

srfsc_profile_api = Blueprint('srfsc_profile_api', __name__, url_prefix='/api/srfsc')


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
