"""Requests the public sends to the Scripps Ranch Fire Safe Council, and their review status."""
from datetime import datetime, timezone

from __init__ import db

REQUEST_TYPES = [
    'Home wildfire assessment',
    'Brush clearing or chipping help',
    'Workshop or presentation',
    'Volunteer opportunity',
    'Question or feedback',
]
REQUEST_STATUSES = ['New', 'In review', 'Resolved']


class SrfscRequest(db.Model):
    __tablename__ = 'srfsc_requests'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    request_type = db.Column(db.String(64), nullable=False)
    message = db.Column(db.Text, nullable=False)
    neighborhood = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(32), nullable=False, default='New')
    admin_note = db.Column(db.Text, nullable=True)
    reviewed_by = db.Column(db.String(255), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    def read(self, include_review=False):
        data = {
            'id': self.id,
            'request_type': self.request_type,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        if include_review:
            # Contact details and council notes are only for admins.
            data.update({
                'name': self.name,
                'email': self.email,
                'neighborhood': self.neighborhood,
                'message': self.message,
                'admin_note': self.admin_note,
                'reviewed_by': self.reviewed_by,
                'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            })
        return data
