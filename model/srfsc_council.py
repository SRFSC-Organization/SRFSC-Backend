"""
Scripps Ranch Fire Safe Council (SRFSC) council models: events, volunteers, hazard reports, RSVPs, updates.

Moved here from capstone2pages/srfsc_backend so one service runs the whole SRFSC site
(pages: /capstone/srfsc/app/). Served by api/srfsc_council.py:
  - SrfscEvent:        clearing days, block meetings, expos shown to the public
  - SrfscVolunteer:    neighbors who sign up to help
  - SrfscHazardReport: resident reports of dead vegetation / hazard trees / blocked firebreaks
  - SrfscRsvp:         a neighbor joining a specific event (one RSVP per email per event)
  - SrfscUpdate:       community news, impact stories, and partner notes (the "trust" feed)
"""
from datetime import datetime, date, timedelta
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from __init__ import db


# Allowed values are shared with api/srfsc_council_validation.py so the two cannot drift.
EVENT_TYPES = ['Clearing Day', 'Block Meeting', 'Fire Safety Expo', 'Board Meeting']
VOLUNTEER_INTERESTS = ['Clearing Day', 'Block Meeting Host', 'Board Leadership', 'Events & Outreach']
HAZARD_TYPES = ['Dead Vegetation', 'Hazard Tree', 'Blocked Firebreak', 'Other']
REPORT_STATUSES = ['New', 'Reviewed', 'Scheduled', 'Resolved']
UPDATE_CATEGORIES = ['News', 'Impact Story', 'Partner']

# Map pins must fall inside this box around Scripps Ranch (south, west, north, east).
SCRIPPS_RANCH_BOUNDS = (32.88, -117.14, 32.96, -117.04)
# Public hazard pins are rounded to about 100 m so they point at an area, not a front door.
PUBLIC_PIN_DECIMALS = 3


class SrfscModelMixin:
    """Shared create/delete helpers that roll back on failure instead of leaving a dirty session."""

    def create(self):
        try:
            db.session.add(self)
            db.session.commit()
            return self
        except IntegrityError:
            db.session.rollback()
            return None

    def delete(self):
        db.session.delete(self)
        db.session.commit()


class SrfscEvent(SrfscModelMixin, db.Model):
    __tablename__ = 'srfsc_events'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    event_type = db.Column(db.String(64), nullable=False)
    event_date = db.Column(db.Date, nullable=False)
    location = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    # Sample events seeded so an empty calendar has something to show. They are labeled on the
    # site, cannot take RSVPs, and admins delete them once the council posts real dates.
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __init__(self, title, event_type, event_date, location, description=None, latitude=None, longitude=None,
                 is_demo=False):
        self.latitude = latitude
        self.longitude = longitude
        self.is_demo = is_demo
        self.title = title
        self.event_type = event_type
        self.event_date = event_date
        self.location = location
        self.description = description

    rsvps = db.relationship('SrfscRsvp', backref='event', cascade='all, delete-orphan', lazy='dynamic')

    def read(self):
        return {
            "id": self.id,
            "title": self.title,
            "event_type": self.event_type,
            "rsvp_count": self.rsvps.count(),
            "event_date": self.event_date.isoformat(),
            "location": self.location,
            "description": self.description,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "is_demo": bool(self.is_demo),
        }


class SrfscVolunteer(SrfscModelMixin, db.Model):
    __tablename__ = 'srfsc_volunteers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(32), nullable=True)
    interest = db.Column(db.String(64), nullable=False)
    street = db.Column(db.String(255), nullable=True)
    message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __init__(self, name, email, interest, phone=None, street=None, message=None):
        self.name = name
        self.email = email
        self.interest = interest
        self.phone = phone
        self.street = street
        self.message = message

    def read(self):
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "interest": self.interest,
            "street": self.street,
            "message": self.message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SrfscHazardReport(SrfscModelMixin, db.Model):
    __tablename__ = 'srfsc_hazard_reports'

    id = db.Column(db.Integer, primary_key=True)
    hazard_type = db.Column(db.String(64), nullable=False)
    location = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    reporter_email = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(32), nullable=False, default='New')
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __init__(self, hazard_type, location, description, reporter_email=None, latitude=None, longitude=None):
        self.latitude = latitude
        self.longitude = longitude
        self.hazard_type = hazard_type
        self.location = location
        self.description = description
        self.reporter_email = reporter_email
        self.status = 'New'

    def map_point(self):
        """What the public map shows for a report: no description or email, and a rounded position."""
        return {
            "id": self.id,
            "hazard_type": self.hazard_type,
            "status": self.status,
            "reported": self.created_at.isoformat() + "Z" if self.created_at else None,   # stored in UTC
            "latitude": round(self.latitude, PUBLIC_PIN_DECIMALS),
            "longitude": round(self.longitude, PUBLIC_PIN_DECIMALS),
        }

    def update_status(self, status):
        self.status = status
        db.session.commit()
        return self

    def read(self, include_contact=False):
        data = {
            "id": self.id,
            "hazard_type": self.hazard_type,
            "location": self.location,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }
        # Reporter email is only exposed to admins
        if include_contact:
            data["reporter_email"] = self.reporter_email
        return data


class SrfscRsvp(SrfscModelMixin, db.Model):
    __tablename__ = 'srfsc_rsvps'
    # Same email cannot RSVP twice for one event; create() returns None on that violation
    __table_args__ = (db.UniqueConstraint('event_id', 'email', name='uq_srfsc_rsvp_event_email'),)

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('srfsc_events.id'), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __init__(self, event_id, name, email):
        self.event_id = event_id
        self.name = name
        self.email = email


class SrfscUpdate(SrfscModelMixin, db.Model):
    __tablename__ = 'srfsc_updates'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(32), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __init__(self, title, body, category):
        self.title = title
        self.body = body
        self.category = category

    def read(self):
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "category": self.category,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


COUNCIL_MODELS = (SrfscEvent, SrfscVolunteer, SrfscHazardReport, SrfscRsvp, SrfscUpdate)


def init_srfsc_council():
    """Create the council tables (only these; never the account tables), label old sample events,
    and seed clearly labeled demo content so an empty site is not blank. Safe to run on every start."""
    db.metadata.create_all(bind=db.engine, tables=[model.__table__ for model in COUNCIL_MODELS])
    _add_missing_columns()
    _place_known_locations()
    _label_sample_events()
    _seed_updates()
    _seed_demo_map()


# Columns added after the tables first shipped (create_all does not alter existing tables).
ADDED_COLUMNS = {
    'srfsc_events': {'latitude': 'FLOAT', 'longitude': 'FLOAT', 'is_demo': 'BOOLEAN NOT NULL DEFAULT 0'},
    'srfsc_hazard_reports': {'latitude': 'FLOAT', 'longitude': 'FLOAT'},
}


def _add_missing_columns():
    inspector = inspect(db.engine)
    for table, columns in ADDED_COLUMNS.items():
        existing = {column['name'] for column in inspector.get_columns(table)}
        for column, column_type in columns.items():
            if column not in existing:
                with db.engine.begin() as connection:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {column_type}'))


# Approximate map positions for council venues, used for events saved before map pins existed.
KNOWN_PLACES = {
    "Canyon edge behind Ellingham Elementary": (32.9238, -117.1032),
    "Scripps Ranch Library Community Room": (32.9174, -117.0898),
    "Scripps Ranch Community Park": (32.9258, -117.0718),
    "Lake Miramar trailhead firebreak": (32.9119, -117.0950),
}


def _place_known_locations():
    unplaced = SrfscEvent.query.filter(SrfscEvent.latitude.is_(None),
                                       SrfscEvent.location.in_(KNOWN_PLACES)).all()
    for event in unplaced:
        event.latitude, event.longitude = KNOWN_PLACES[event.location]
    if unplaced:
        db.session.commit()


# Placeholder events that earlier versions seeded with made-up dates. They were never confirmed
# by the council, so databases that still hold them (unedited) get them labeled as demo data.
LEGACY_STARTER_EVENTS = {
    ("Monthly Clearing Day", "Canyon edge behind Ellingham Elementary"),
    ("Block Meeting: Defensible Space 101", "Scripps Ranch Library Community Room"),
    ("Fire Safety Expo", "Scripps Ranch Community Park"),
    ("November Clearing Day", "Lake Miramar trailhead firebreak"),
}


def _label_sample_events():
    changed = False
    for event in SrfscEvent.query.filter(SrfscEvent.is_demo.is_(False)).all():
        legacy = (event.title, event.location) in LEGACY_STARTER_EVENTS
        if legacy or event.title in DEMO_EVENT_TITLES:
            event.is_demo = True
            if not event.title.endswith(DEMO_SUFFIX):
                event.title += DEMO_SUFFIX
            if legacy:
                event.description = DEMO_NOTE
            changed = True
    if changed:
        db.session.commit()


# Demo content, clearly labeled, so the live map and calendar have something to show until the
# council posts real events and residents send real reports.
DEMO_HAZARDS = [
    ("Hazard Tree", "Canyon behind Ironwood Rd (demo)", "Reviewed", 32.9196, -117.0811),
    ("Dead Vegetation", "Slope above Pomerado Rd (demo)", "New", 32.9302, -117.0876),
    ("Blocked Firebreak", "Fire road east of Lake Miramar (demo)", "Scheduled", 32.9151, -117.0868),
]
DEMO_EVENTS = [
    # (days from today, title, type, location, latitude, longitude)
    (16, "Block Meeting: Evacuation Routes (demo)", "Block Meeting", "Cypress Canyon Park", 32.9221, -117.0784),
    (30, "Council Board Meeting (demo)", "Board Meeting", "SRFSC office, 9903 Businesspark Ave", 32.9048, -117.1137),
]
DEMO_SUFFIX = " (demo)"
DEMO_EVENT_TITLES = {title for _, title, *_ in DEMO_EVENTS}
DEMO_NOTE = "DEMO - sample event, not a real council date. Call 858-201-3711 for the next one."
DEMO_HAZARD_NOTE = "DEMO - sample hazard so the live map has something to show."


def remove_demo_events():
    """Delete every sample event. Called when an admin posts a real one."""
    removed = SrfscEvent.query.filter(SrfscEvent.is_demo.is_(True)).delete(synchronize_session=False)
    if removed:
        db.session.commit()
    return removed


def _seed_demo_map():
    """Demo hazard pins when there are no hazard reports at all, and demo events when the council
    has never posted a real event. Real data is never changed, and running again adds nothing."""
    if not SrfscHazardReport.query.first():
        for hazard_type, location, status, latitude, longitude in DEMO_HAZARDS:
            report = SrfscHazardReport(hazard_type, location, DEMO_HAZARD_NOTE, latitude=latitude, longitude=longitude)
            report.status = status
            db.session.add(report)
    # Any event at all (real, or demo already seeded) means the calendar is not empty.
    if SrfscEvent.query.first() is None:
        for days, title, event_type, location, latitude, longitude in DEMO_EVENTS:
            db.session.add(SrfscEvent(title, event_type, date.today() + timedelta(days=days), location,
                                      DEMO_NOTE, latitude, longitude, is_demo=True))
    db.session.commit()


def _seed_updates():
    with db.session.no_autoflush:
        if SrfscUpdate.query.first():
            return
        # Facts below come from srfiresafe.org; they seed the trust feed until admins post real news.
        seed_updates = [
            SrfscUpdate("Why we exist",
                        "The 2003 Cedar Fire destroyed 312 homes in Scripps Ranch. Neighbors founded the council "
                        "in 2004 so the next fire meets cleared canyons and prepared households.",
                        "Impact Story"),
            SrfscUpdate("650+ residential firebreaks",
                        "Volunteers and contractors have established more than 650 residential firebreaks and "
                        "removed 340 dangerous trees along canyon edges.",
                        "Impact Story"),
            SrfscUpdate("Working with the agencies",
                        "SRFSC coordinates with CAL FIRE, San Diego Fire-Rescue, and the California Conservation "
                        "Corps on fuel reduction and community education.",
                        "Partner"),
        ]
        for update in seed_updates:
            update.create()
