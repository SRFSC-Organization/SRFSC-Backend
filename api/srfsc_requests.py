"""Public request survey for the fire council, reviewed by council Admin accounts."""
import re
from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from __init__ import db
from api.authorize import token_required
from model.srfsc_request import REQUEST_STATUSES, REQUEST_TYPES, SrfscRequest

srfsc_requests_api = Blueprint('srfsc_requests_api', __name__, url_prefix='/api/srfsc/requests')

EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _text(body, field, label, min_length=1, max_length=100, required=True):
    """Return (clean value, error message) for one text field."""
    value = body.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        return (None, f'{label} is required.') if required else (None, None)
    if not isinstance(value, str):
        return None, f'{label} must be text.'
    value = value.strip()
    if not min_length <= len(value) <= max_length:
        return None, f'{label} must be between {min_length} and {max_length} characters.'
    return value, None


def validate_request(body):
    """Check a survey submission. Returns (clean fields, error message); exactly one is None."""
    if not isinstance(body, dict):
        return None, 'Send the survey answers as a JSON object.'
    checks = [
        ('name', _text(body, 'name', 'Name', min_length=2)),
        ('email', _text(body, 'email', 'Email', max_length=255)),
        ('request_type', _text(body, 'request_type', 'Request type', max_length=64)),
        ('message', _text(body, 'message', 'Message', min_length=10, max_length=2000)),
        ('neighborhood', _text(body, 'neighborhood', 'Neighborhood', required=False)),
    ]
    clean = {}
    for field, (value, error) in checks:
        if error:
            return None, error
        clean[field] = value
    if not EMAIL_PATTERN.match(clean['email']):
        return None, 'Enter a valid email address so the council can reply.'
    if clean['request_type'] not in REQUEST_TYPES:
        return None, f"Request type must be one of: {', '.join(REQUEST_TYPES)}."
    clean['email'] = clean['email'].lower()
    return clean, None


def _commit(error_message):
    try:
        db.session.commit()
        return None
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(error_message)
        return jsonify(message=f'{error_message} Please try again.'), 500


@srfsc_requests_api.get('/options')
def options():
    return jsonify(request_types=REQUEST_TYPES, statuses=REQUEST_STATUSES)


@srfsc_requests_api.post('')
@srfsc_requests_api.post('/')
def submit_request():
    clean, error = validate_request(request.get_json(silent=True))
    if error:
        return jsonify(message=error), 400
    survey = SrfscRequest(**clean)
    db.session.add(survey)
    failed = _commit('Your request could not be saved.')
    if failed:
        return failed
    return jsonify(id=survey.id, status=survey.status,
                   message=f'Thank you, {survey.name}. Request #{survey.id} was sent to the council.'), 201


@srfsc_requests_api.get('')
@srfsc_requests_api.get('/')
@token_required('Admin')
def list_requests():
    status = request.args.get('status')
    query = SrfscRequest.query
    if status:
        if status not in REQUEST_STATUSES:
            return jsonify(message=f"Status must be one of: {', '.join(REQUEST_STATUSES)}."), 400
        query = query.filter_by(status=status)
    requests = query.order_by(SrfscRequest.created_at.desc(), SrfscRequest.id.desc()).all()
    return jsonify([survey.read(include_review=True) for survey in requests])


@srfsc_requests_api.put('/<int:request_id>')
@token_required('Admin')
def review_request(request_id):
    survey = db.session.get(SrfscRequest, request_id)
    if survey is None:
        return jsonify(message=f'Request #{request_id} was not found.'), 404
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not set(body) <= {'status', 'admin_note'} or 'status' not in body:
        return jsonify(message='Send a status, and optionally an admin_note.'), 400
    if body['status'] not in REQUEST_STATUSES:
        return jsonify(message=f"Status must be one of: {', '.join(REQUEST_STATUSES)}."), 400
    note, error = _text(body, 'admin_note', 'Admin note', max_length=2000, required=False)
    if error:
        return jsonify(message=error), 400
    survey.status = body['status']
    survey.admin_note = note
    survey.reviewed_by = g.current_user.uid
    survey.reviewed_at = datetime.now(timezone.utc)
    failed = _commit(f'Request #{request_id} could not be updated.')
    if failed:
        return failed
    return jsonify(survey.read(include_review=True))
