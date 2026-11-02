"""Public request survey for the fire council (with optional photos), reviewed by council Admin accounts."""
import io
import re
from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, g, jsonify, request
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.exc import SQLAlchemyError

from __init__ import db
from api.authorize import token_required
from model.srfsc_request import MAX_PHOTOS, REQUEST_STATUSES, REQUEST_TYPES, SrfscRequest, SrfscRequestPhoto

srfsc_requests_api = Blueprint('srfsc_requests_api', __name__, url_prefix='/api/srfsc/requests')

EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
PHOTO_FORMATS = {'JPEG', 'PNG', 'WEBP'}
PHOTO_MAX_SIDE = 1600           # stored photos are at most this many pixels wide or tall
PHOTO_MAX_PIXELS = 40_000_000   # refuse anything larger before decoding it


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


def read_photo(upload):
    """Decode one uploaded photo and re-save it as a JPEG without metadata (no GPS location).

    Returns (jpeg bytes, width, height, error message); the error is None when the photo is usable.
    """
    try:
        image = Image.open(upload.stream)
        if image.format not in PHOTO_FORMATS:
            return None, 0, 0, 'Photos must be JPEG, PNG, or WebP images.'
        if image.width * image.height > PHOTO_MAX_PIXELS:
            return None, 0, 0, 'That photo is too large. Please send a smaller one.'
        image = ImageOps.exif_transpose(image).convert('RGB')
        image.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
        output = io.BytesIO()
        image.save(output, 'JPEG', quality=82, optimize=True)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None, 0, 0, 'One of the photos could not be read. Please choose a JPEG, PNG, or WebP image.'
    return output.getvalue(), image.width, image.height, None


def read_submission():
    """The survey fields and photo uploads, from a JSON body or a multipart form. Returns (fields, uploads)."""
    if request.mimetype == 'multipart/form-data':
        uploads = [upload for upload in request.files.getlist('photos') if upload and upload.filename]
        return request.form.to_dict(), uploads
    return request.get_json(silent=True), []


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
    body, uploads = read_submission()
    clean, error = validate_request(body)
    if error:
        return jsonify(message=error), 400
    if len(uploads) > MAX_PHOTOS:
        return jsonify(message=f'Please attach at most {MAX_PHOTOS} photos.'), 400
    survey = SrfscRequest(**clean)
    for upload in uploads:
        data, width, height, error = read_photo(upload)
        if error:
            return jsonify(message=error), 400
        survey.photos.append(SrfscRequestPhoto(data=data, width=width, height=height))
    db.session.add(survey)
    failed = _commit('Your request could not be saved.')
    if failed:
        return failed
    photos = f' with {len(survey.photos)} photo{"s" if len(survey.photos) != 1 else ""}' if survey.photos else ''
    return jsonify(id=survey.id, status=survey.status,
                   message=f'Thank you, {survey.name}. Request #{survey.id}{photos} was sent to the council.'), 201


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


@srfsc_requests_api.get('/<int:request_id>')
@token_required('Admin')
def get_request(request_id):
    survey = db.session.get(SrfscRequest, request_id)
    if survey is None:
        return jsonify(message=f'Request #{request_id} was not found.'), 404
    return jsonify(survey.read(include_review=True))


@srfsc_requests_api.get('/<int:request_id>/photos/<int:photo_id>')
@token_required('Admin')
def get_photo(request_id, photo_id):
    photo = db.session.get(SrfscRequestPhoto, photo_id)
    if photo is None or photo.request_id != request_id:
        return jsonify(message='Photo not found.'), 404
    response = Response(photo.data, mimetype=photo.content_type)
    # Private: residents' photos must never be kept by a shared cache.
    response.headers['Cache-Control'] = 'private, max-age=3600'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


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
