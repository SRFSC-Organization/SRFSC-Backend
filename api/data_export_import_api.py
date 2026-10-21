"""
Data Export/Import API
Provides comprehensive endpoints for exporting and importing ALL database data.
Used by db_migrate and db_restore scripts for database migration.
"""

from flask import Blueprint, request, jsonify, g
from flask_restful import Api, Resource
from api.authorize import token_required
from __init__ import db

# Import all models
from model.user import User, Section
from model.post import Post
from model.microblog import MicroBlog, Topic
from model.classroom import Classroom
from model.feedback import Feedback
from model.study import Study
from model.persona import Persona, UserPersona
from model.leaderboard import ScoreCounterEvent, ElementaryLeaderboardEvent
from model.skill_snapshot import SkillSnapshot

data_export_import_api = Blueprint('data_export_import_api', __name__, url_prefix='/api/export')
api = Api(data_export_import_api)


def _parse_dt(value):
    """Parse an ISO-8601 timestamp emitted by a model read(); None if unusable."""
    if not value:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


# Every persisted model, keyed by the data type name used across the migration
# scripts. Kept in one place so /counts, the exporters and the importers cannot
# drift apart -- a missing entry here is what silently loses a table.
MIGRATED_MODELS = {
    'sections':                 Section,
    'users':                    User,
    'topics':                   Topic,
    'microblogs':               MicroBlog,
    'posts':                    Post,
    'classrooms':               Classroom,
    'feedback':                 Feedback,
    'study':                    Study,
    'personas':                 Persona,
    'user_personas':            UserPersona,
    'leaderboard':              ScoreCounterEvent,
    'elementary_leaderboard':   ElementaryLeaderboardEvent,
    'skill_snapshots':          SkillSnapshot,
}


class ExportCounts(Resource):
    """Row counts for every migrated table, used to verify a migration moved everything."""

    @token_required()
    def get(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403

        counts = {}
        for name, model in MIGRATED_MODELS.items():
            try:
                counts[name] = model.query.count()
            except Exception as e:
                counts[name] = f'error: {e}'

        # Association tables have no model class of their own.
        try:
            from model.classroom import classroom_student
            counts['classroom_students'] = db.session.query(classroom_student).count()
        except Exception as e:
            counts['classroom_students'] = f'error: {e}'
        try:
            from model.user import UserSection
            counts['user_sections'] = UserSection.query.count()
        except Exception as e:
            counts['user_sections'] = f'error: {e}'

        return jsonify({'counts': counts})


class ExportAllData(Resource):
    """
    Export ALL data from the database in a single comprehensive response.
    This eliminates the need to call multiple individual endpoints.
    """

    @token_required()
    def get(self):
        """
        GET /api/export/all
        Returns all database data in a structured format.
        Requires admin authentication.
        """
        current_user = g.current_user

        # Only allow admins to export all data
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required to export data'}, 403

        try:
            # Export all data from each table
            export_data = {
                'sections': self._export_sections(),
                'users': self._export_users(),
                'topics': self._export_topics(),
                'microblogs': self._export_microblogs(),
                'posts': self._export_posts(),
                'classrooms': self._export_classrooms(),
                'feedback': self._export_feedback(),
                'study': self._export_study(),
                'personas': self._export_personas(),
                'user_personas': self._export_user_personas(),
                'leaderboard': self._export_event_table(ScoreCounterEvent),
                'elementary_leaderboard': self._export_event_table(ElementaryLeaderboardEvent),
                'skill_snapshots': self._export_skill_snapshots(),
            }

            # Add metadata
            from datetime import datetime
            export_data['_metadata'] = {
                'exported_at': datetime.utcnow().isoformat(),
                'total_records': sum(len(v) if isinstance(v, list) else 0 for v in export_data.values() if v),
                'tables': list(export_data.keys())
            }

            return jsonify(export_data)

        except Exception as e:
            return {'message': f'Export failed: {str(e)}'}, 500

    def _export_sections(self):
        """Export all sections"""
        sections = Section.query.all()
        return [s.read() for s in sections]

    def _export_users(self):
        """Export all users with their section associations"""
        users = User.query.all()
        result = []
        for user in users:
            user_data = user.read()
            user_data['srfsc_profile'] = user._srfsc_profile
            # Include sections for each user
            user_data['sections'] = [s.read() for s in user.sections]
            result.append(user_data)
        return result

    def _export_topics(self):
        """Export all microblog topics"""
        topics = Topic.query.all()
        return [t.read() for t in topics]

    def _export_microblogs(self):
        """Export all microblogs"""
        microblogs = MicroBlog.query.all()
        result = []
        for mb in microblogs:
            mb_data = mb.read()
            # Include user uid and topic path for easier restoration
            if mb.user:
                mb_data['userUid'] = mb.user.uid
            if mb.topic:
                mb_data['topicPath'] = mb.topic._page_path
            result.append(mb_data)
        return result

    def _export_posts(self):
        """Export all social media posts"""
        posts = Post.query.all()
        result = []
        for post in posts:
            post_data = post.read()
            # Include user uid for easier restoration
            if post.user:
                post_data['userUid'] = post.user.uid
            result.append(post_data)
        return result

    def _export_classrooms(self):
        """Export all classrooms with student associations"""
        classrooms = Classroom.query.all()
        result = []
        for classroom in classrooms:
            classroom_data = classroom.to_dict()
            # Include owner uid
            owner = User.query.get(classroom.owner_teacher_id)
            if owner:
                classroom_data['ownerUid'] = owner.uid
            # Include student uids
            classroom_data['studentUids'] = [s.uid for s in classroom.students.all()]
            result.append(classroom_data)
        return result

    def _export_feedback(self):
        """Export all feedback"""
        feedback_items = Feedback.query.all()
        return [f.read() for f in feedback_items]

    def _export_study(self):
        """Export all study tracker records"""
        study_records = Study.query.all()
        result = []
        for study in study_records:
            study_data = study.to_dict()
            # Include user uid
            if study.user_id:
                user = User.query.get(study.user_id)
                if user:
                    study_data['userUid'] = user.uid
            result.append(study_data)
        return result

    def _export_personas(self):
        """Export all personas"""
        personas = Persona.query.all()
        return [p.read() for p in personas]

    def _export_event_table(self, model):
        """Export one of the leaderboard event tables"""
        return [
            {
                'id':        event.id,
                'userUid':   event.user.uid if event.user else None,
                'payload':   event._payload or {},
                'timestamp': event._timestamp.isoformat() if event._timestamp else None,
            }
            for event in model.query.order_by(model.id).all()
        ]

    def _export_skill_snapshots(self):
        """Export all skill snapshots"""
        result = []
        for snapshot in SkillSnapshot.query.order_by(SkillSnapshot.id).all():
            data = snapshot.read()
            data['userUid'] = snapshot.user.uid if snapshot.user else None
            result.append(data)
        return result

    def _export_user_personas(self):
        """Export user-persona associations"""
        user_personas = UserPersona.query.all()
        result = []
        for up in user_personas:
            result.append({
                'userUid': up.user.uid if up.user else None,
                'personaAlias': up.persona.alias if up.persona else None,
                'weight': up.weight,
                'selectedAt': up.selected_at.isoformat() if up.selected_at else None
            })
        return result


class ImportAllData(Resource):
    """
    Import ALL data into the database from a comprehensive payload.
    Handles proper ordering and relationship restoration.
    """

    @token_required()
    def post(self):
        """
        POST /api/export/import
        Imports all database data from the request payload.
        Requires admin authentication.

        Expected payload format:
        {
            "sections": [...],
            "users": [...],
            "topics": [...],
            "microblogs": [...],
            "posts": [...],
            "classrooms": [...],
            "feedback": [...],
            "study": [...],
            "personas": [...],
            "user_personas": [...]
        }
        """
        current_user = g.current_user

        # Only allow admins to import data
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required to import data'}, 403

        data = request.get_json()
        if not data:
            return {'message': 'No data provided'}, 400

        results = {
            'sections': {'imported': 0, 'failed': 0, 'errors': []},
            'users': {'imported': 0, 'failed': 0, 'errors': []},
            'topics': {'imported': 0, 'failed': 0, 'errors': []},
            'microblogs': {'imported': 0, 'failed': 0, 'errors': []},
            'posts': {'imported': 0, 'failed': 0, 'errors': []},
            'classrooms': {'imported': 0, 'failed': 0, 'errors': []},
            'feedback': {'imported': 0, 'failed': 0, 'errors': []},
            'study': {'imported': 0, 'failed': 0, 'errors': []},
            'personas': {'imported': 0, 'failed': 0, 'errors': []},
            'user_personas': {'imported': 0, 'failed': 0, 'errors': []},
            'leaderboard': {'imported': 0, 'failed': 0, 'errors': []},
            'elementary_leaderboard': {'imported': 0, 'failed': 0, 'errors': []},
            'skill_snapshots': {'imported': 0, 'failed': 0, 'errors': []},
        }

        try:
            # Import in proper dependency order
            # 1. Sections (no dependencies)
            if 'sections' in data:
                results['sections'] = self._import_sections(data['sections'])

            # 2. Users (depends on sections)
            if 'users' in data:
                results['users'] = self._import_users(data['users'])

            # 3. Topics (no dependencies)
            if 'topics' in data:
                results['topics'] = self._import_topics(data['topics'])

            # 4. Personas (no dependencies)
            if 'personas' in data:
                results['personas'] = self._import_personas(data['personas'])

            # 5. User Personas (depends on users and personas)
            if 'user_personas' in data:
                results['user_personas'] = self._import_user_personas(data['user_personas'])

            # 6. Microblogs (depends on users and topics)
            if 'microblogs' in data:
                results['microblogs'] = self._import_microblogs(data['microblogs'])

            # 7. Posts (depends on users)
            if 'posts' in data:
                results['posts'] = self._import_posts(data['posts'])

            # 8. Classrooms (depends on users)
            if 'classrooms' in data:
                results['classrooms'] = self._import_classrooms(data['classrooms'])

            # 9. Feedback (no critical dependencies)
            if 'feedback' in data:
                results['feedback'] = self._import_feedback(data['feedback'])

            # 10. Study (depends on users)
            if 'study' in data:
                results['study'] = self._import_study(data['study'])

            # 11. Leaderboard events (depend on users, but tolerate orphans)
            if 'leaderboard' in data:
                results['leaderboard'] = self._import_leaderboard(data['leaderboard'])

            if 'elementary_leaderboard' in data:
                results['elementary_leaderboard'] = self._import_elementary_leaderboard(
                    data['elementary_leaderboard']
                )

            # 12. Skill snapshots (depend on users)
            if 'skill_snapshots' in data:
                results['skill_snapshots'] = self._import_skill_snapshots(data['skill_snapshots'])

            return jsonify({
                'message': 'Import completed',
                'results': results
            })

        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500

    def _import_sections(self, sections_data):
        """Import sections"""
        imported = 0
        failed = 0
        errors = []

        for section_data in sections_data:
            try:
                # Check if already exists
                existing = Section.query.filter_by(_abbreviation=section_data.get('abbreviation')).first()
                if existing:
                    continue

                section = Section(
                    name=section_data.get('name'),
                    abbreviation=section_data.get('abbreviation')
                )
                section.create()
                imported += 1
            except Exception as e:
                failed += 1
                errors.append(f"Section {section_data.get('abbreviation')}: {str(e)}")

        return {'imported': imported, 'failed': failed, 'errors': errors}

    def _import_users(self, users_data):
        """Import users with their section associations"""
        imported = 0
        failed = 0
        errors = []

        for user_data in users_data:
            try:
                # Check if user already exists
                existing = User.query.filter_by(_uid=user_data.get('uid')).first()
                if existing:
                    continue

                # Create new user (note: email is not a constructor param, set via property after)
                user = User(
                    name=user_data.get('name'),
                    uid=user_data.get('uid'),
                    password=user_data.get('password', ''),
                    sid=user_data.get('sid'),
                    role=user_data.get('role', 'User'),
                    pfp=user_data.get('pfp'),
                    kasm_server_needed=user_data.get('kasm_server_needed', False),
                    grade_data=user_data.get('grade_data') or user_data.get('gradeData'),
                    ap_exam=user_data.get('ap_exam') or user_data.get('apExam'),
                    school=user_data.get('school'),
                    classes=user_data.get('class') or user_data.get('_class'),
                    game_profile=user_data.get('game_profile') or user_data.get('gameProfile')
                )

                user._srfsc_profile = user_data.get('srfsc_profile')
                # Set email via property (not a constructor param)
                if user_data.get('email'):
                    user.email = user_data.get('email')

                # Add sections if provided
                if 'sections' in user_data and user_data['sections']:
                    for section_data in user_data['sections']:
                        section_abbrev = section_data.get('abbreviation')
                        if section_abbrev:
                            section = Section.query.filter_by(_abbreviation=section_abbrev).first()
                            if section:
                                user.sections.append(section)

                user.create()
                imported += 1
            except Exception as e:
                failed += 1
                errors.append(f"User {user_data.get('uid')}: {str(e)}")

        return {'imported': imported, 'failed': failed, 'errors': errors}

    def _import_topics(self, topics_data):
        """Import microblog topics"""
        imported = 0
        failed = 0
        errors = []

        for topic_data in topics_data:
            try:
                page_path = topic_data.get('pagePath') or topic_data.get('page_path')
                existing = Topic.query.filter_by(_page_path=page_path).first()
                if existing:
                    continue

                topic = Topic(
                    page_path=page_path,
                    page_title=topic_data.get('pageTitle') or topic_data.get('page_title'),
                    page_description=topic_data.get('pageDescription') or topic_data.get('page_description'),
                    display_name=topic_data.get('displayName') or topic_data.get('display_name'),
                    color=topic_data.get('color', '#007bff'),
                    icon=topic_data.get('icon'),
                    allow_anonymous=topic_data.get('allowAnonymous') or topic_data.get('allow_anonymous', False),
                    moderated=topic_data.get('moderated', False),
                    max_posts_per_user=topic_data.get('maxPostsPerUser') or topic_data.get('max_posts_per_user', 10),
                    settings=topic_data.get('settings', {})
                )
                topic.create()
                imported += 1
            except Exception as e:
                failed += 1
                errors.append(f"Topic {page_path}: {str(e)}")

        return {'imported': imported, 'failed': failed, 'errors': errors}

    def _import_microblogs(self, microblogs_data):
        """Import microblogs"""
        imported = 0
        failed = 0
        errors = []

        for mb_data in microblogs_data:
            try:
                # Find user by uid
                user_uid = mb_data.get('userUid')
                user = User.query.filter_by(_uid=user_uid).first() if user_uid else None
                if not user:
                    failed += 1
                    continue

                # Find topic by path
                topic_id = None
                topic_path = mb_data.get('topicPath')
                if topic_path:
                    topic = Topic.query.filter_by(_page_path=topic_path).first()
                    if topic:
                        topic_id = topic.id

                microblog = MicroBlog(
                    user_id=user.id,
                    content=mb_data.get('content'),
                    topic_id=topic_id,
                    data=mb_data.get('data', {})
                )
                microblog.create()
                imported += 1
            except Exception as e:
                failed += 1
                errors.append(f"Microblog: {str(e)}")

        return {'imported': imported, 'failed': failed, 'errors': errors}

    def _import_posts(self, posts_data):
        """Import social media posts"""
        imported = 0
        failed = 0
        errors = []

        # Map old IDs to new IDs for reply handling
        id_mapping = {}

        # First pass: top-level posts
        top_level = [p for p in posts_data if not (p.get('parent_id') or p.get('parentId'))]
        replies = [p for p in posts_data if p.get('parent_id') or p.get('parentId')]

        for post_data in top_level:
            try:
                user_uid = post_data.get('userUid')
                user = User.query.filter_by(_uid=user_uid).first() if user_uid else None
                if not user:
                    failed += 1
                    continue

                post = Post(
                    user_id=user.id,
                    content=post_data.get('content'),
                    grade_received=post_data.get('gradeReceived') or post_data.get('grade_received'),
                    page_url=post_data.get('pageUrl') or post_data.get('page_url'),
                    page_title=post_data.get('pageTitle') or post_data.get('page_title')
                )
                created = post.create()
                if created:
                    old_id = post_data.get('id')
                    if old_id:
                        id_mapping[old_id] = created.id
                    imported += 1
            except Exception as e:
                failed += 1
                errors.append(f"Post: {str(e)}")

        # Second pass: replies
        for reply_data in replies:
            try:
                user_uid = reply_data.get('userUid')
                user = User.query.filter_by(_uid=user_uid).first() if user_uid else None
                if not user:
                    failed += 1
                    continue

                old_parent_id = reply_data.get('parentId') or reply_data.get('parent_id')
                new_parent_id = id_mapping.get(old_parent_id)
                if not new_parent_id:
                    failed += 1
                    continue

                reply = Post(
                    user_id=user.id,
                    content=reply_data.get('content'),
                    parent_id=new_parent_id
                )
                reply.create()
                imported += 1
            except Exception as e:
                failed += 1
                errors.append(f"Reply: {str(e)}")

        return {'imported': imported, 'failed': failed, 'errors': errors}

    def _import_classrooms(self, classrooms_data):
        """Import classrooms with student associations"""
        imported = 0
        failed = 0
        errors = []

        for classroom_data in classrooms_data:
            try:
                # Find owner by uid
                owner_uid = classroom_data.get('ownerUid')
                owner = User.query.filter_by(_uid=owner_uid).first() if owner_uid else None
                if not owner:
                    failed += 1
                    continue

                classroom = Classroom(
                    name=classroom_data.get('name'),
                    school_name=classroom_data.get('school_name') or classroom_data.get('schoolName'),
                    owner_teacher_id=owner.id,
                    status=classroom_data.get('status', 'active')
                )
                classroom.create()

                # Add students
                student_uids = classroom_data.get('studentUids', [])
                for student_uid in student_uids:
                    student = User.query.filter_by(_uid=student_uid).first()
                    if student:
                        classroom.students.append(student)

                db.session.commit()
                imported += 1
            except Exception as e:
                failed += 1
                errors.append(f"Classroom: {str(e)}")

        return {'imported': imported, 'failed': failed, 'errors': errors}

    def _import_feedback(self, feedback_data):
        """Import feedback"""
        imported = 0
        failed = 0
        errors = []

        for fb_data in feedback_data:
            try:
                feedback = Feedback(
                    title=fb_data.get('title'),
                    body=fb_data.get('body'),
                    type=fb_data.get('type', 'Other'),
                    github_username=fb_data.get('github_username')
                )
                feedback.github_issue_url = fb_data.get('github_issue_url')
                feedback.create()
                imported += 1
            except Exception as e:
                failed += 1
                errors.append(f"Feedback: {str(e)}")

        return {'imported': imported, 'failed': failed, 'errors': errors}

    def _import_study(self, study_data):
        """Import study tracker records"""
        imported = 0
        failed = 0
        errors = []

        for study_record in study_data:
            try:
                # Find user by uid
                user_uid = study_record.get('userUid')
                user = User.query.filter_by(_uid=user_uid).first() if user_uid else None

                study = Study(
                    user_id=user.id if user else None,
                    topic=study_record.get('topic'),
                    subtopic=study_record.get('subtopic'),
                    studied=study_record.get('studied', False),
                    timestamp=study_record.get('timestamp')
                )
                study.create()
                imported += 1
            except Exception as e:
                failed += 1
                errors.append(f"Study: {str(e)}")

        return {'imported': imported, 'failed': failed, 'errors': errors}

    def _import_personas(self, personas_data):
        """Import personas"""
        imported = 0
        failed = 0
        errors = []

        for persona_data in personas_data:
            try:
                existing = Persona.query.filter_by(_alias=persona_data.get('alias')).first()
                if existing:
                    continue

                persona = Persona(
                    _alias=persona_data.get('alias'),
                    _category=persona_data.get('category'),
                    _bio_map=persona_data.get('bio_map') or persona_data.get('bioMap'),
                    _empathy_map=persona_data.get('empathy_map') or persona_data.get('empathyMap')
                )
                persona.create()
                imported += 1
            except Exception as e:
                failed += 1
                errors.append(f"Persona: {str(e)}")

        return {'imported': imported, 'failed': failed, 'errors': errors}

    def _import_user_personas(self, user_personas_data):
        """Import user-persona associations"""
        imported = 0
        failed = 0
        errors = []

        for up_data in user_personas_data:
            try:
                user_uid = up_data.get('userUid')
                persona_alias = up_data.get('personaAlias')

                user = User.query.filter_by(_uid=user_uid).first() if user_uid else None
                persona = Persona.query.filter_by(_alias=persona_alias).first() if persona_alias else None

                if not user or not persona:
                    failed += 1
                    continue

                # Check if already exists
                existing = UserPersona.query.filter_by(user_id=user.id, persona_id=persona.id).first()
                if existing:
                    continue

                user_persona = UserPersona(
                    user=user,
                    persona=persona,
                    weight=up_data.get('weight', 1)
                )
                db.session.add(user_persona)
                db.session.commit()
                imported += 1
            except Exception as e:
                failed += 1
                errors.append(f"UserPersona: {str(e)}")

        return {'imported': imported, 'failed': failed, 'errors': errors}

    def _import_events(self, events_data, model, label):
        """Import leaderboard events, preserving the original timestamp."""
        imported = 0
        failed = 0
        errors = []

        for event_data in events_data:
            try:
                user_uid = event_data.get('userUid')
                user = User.query.filter_by(_uid=user_uid).first() if user_uid else None

                # user_id is nullable on both event tables: an event whose author
                # is gone is still real history and must not be dropped.
                event = model(
                    payload=event_data.get('payload') or {},
                    user_id=user.id if user else None,
                )
                timestamp = _parse_dt(event_data.get('timestamp'))
                if timestamp:
                    event._timestamp = timestamp

                db.session.add(event)
                db.session.commit()
                imported += 1
            except Exception as e:
                db.session.rollback()
                failed += 1
                errors.append(f"{label}: {str(e)}")

        return {'imported': imported, 'failed': failed, 'errors': errors}

    def _import_leaderboard(self, events_data):
        """Import SCORE_COUNTER leaderboard events"""
        return self._import_events(events_data, ScoreCounterEvent, 'Leaderboard')

    def _import_elementary_leaderboard(self, events_data):
        """Import elementary leaderboard events"""
        return self._import_events(events_data, ElementaryLeaderboardEvent, 'ElementaryLeaderboard')

    def _import_skill_snapshots(self, snapshots_data):
        """Import skill snapshots"""
        imported = 0
        failed = 0
        errors = []

        for snapshot_data in snapshots_data:
            try:
                user_uid = snapshot_data.get('userUid')
                user = User.query.filter_by(_uid=user_uid).first() if user_uid else None
                if not user:
                    # user_id is NOT NULL here, so an orphan snapshot cannot be stored.
                    failed += 1
                    errors.append(f"SkillSnapshot: no user for uid {user_uid!r}")
                    continue

                snapshot = SkillSnapshot(
                    user_id=user.id,
                    project_name=snapshot_data.get('project_name'),
                    coding_ability=snapshot_data.get('coding_ability'),
                    collaboration=snapshot_data.get('collaboration'),
                    problem_solving=snapshot_data.get('problem_solving'),
                    initiative=snapshot_data.get('initiative'),
                )
                snapshot_date = _parse_dt(snapshot_data.get('snapshot_date'))
                if snapshot_date:
                    snapshot.snapshot_date = snapshot_date

                db.session.add(snapshot)
                db.session.commit()
                imported += 1
            except Exception as e:
                db.session.rollback()
                failed += 1
                errors.append(f"SkillSnapshot: {str(e)}")

        return {'imported': imported, 'failed': failed, 'errors': errors}


# Individual export endpoints for chunked exports
class ExportSections(Resource):
    @token_required()
    def get(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        sections = Section.query.all()
        return jsonify({'sections': [s.read() for s in sections], 'count': len(sections)})

class ExportUsers(Resource):
    @token_required()
    def get(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403

        # Always use pagination to prevent timeouts
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        # Paginate the query with eager loading to avoid N+1 queries
        from sqlalchemy.orm import joinedload
        pagination = User.query.options(joinedload(User.sections)).paginate(
            page=page, per_page=per_page, error_out=False
        )

        result = []
        for user in pagination.items:
            user_data = user.read()
            user_data['srfsc_profile'] = user._srfsc_profile
            user_data['sections'] = [s.read() for s in user.sections]
            result.append(user_data)

        return jsonify({
            'users': result,
            'count': len(result),
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'has_next': pagination.has_next,
            'has_prev': pagination.has_prev
        })

class ExportTopics(Resource):
    @token_required()
    def get(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403

        # Always use pagination to prevent timeouts
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        pagination = Topic.query.paginate(page=page, per_page=per_page, error_out=False)

        return jsonify({
            'topics': [t.read() for t in pagination.items],
            'count': len(pagination.items),
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'has_next': pagination.has_next,
            'has_prev': pagination.has_prev
        })

class ExportMicroblogs(Resource):
    @token_required()
    def get(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403

        # Always use pagination to prevent timeouts
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        pagination = MicroBlog.query.paginate(page=page, per_page=per_page, error_out=False)

        result = []
        for mb in pagination.items:
            mb_data = mb.read()
            if mb.user:
                mb_data['userUid'] = mb.user.uid
            if mb.topic:
                mb_data['topicPath'] = mb.topic._page_path
            result.append(mb_data)

        return jsonify({
            'microblogs': result,
            'count': len(result),
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'has_next': pagination.has_next,
            'has_prev': pagination.has_prev
        })

class ExportPosts(Resource):
    @token_required()
    def get(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403

        # Always use pagination to prevent timeouts
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        pagination = Post.query.paginate(page=page, per_page=per_page, error_out=False)

        result = []
        for post in pagination.items:
            post_data = post.read()
            if post.user:
                post_data['userUid'] = post.user.uid
            result.append(post_data)

        return jsonify({
            'posts': result,
            'count': len(result),
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'has_next': pagination.has_next,
            'has_prev': pagination.has_prev
        })

class ExportClassrooms(Resource):
    @token_required()
    def get(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        classrooms = Classroom.query.all()
        result = []
        for classroom in classrooms:
            classroom_data = classroom.to_dict()
            owner = User.query.get(classroom.owner_teacher_id)
            if owner:
                classroom_data['ownerUid'] = owner.uid
            classroom_data['studentUids'] = [s.uid for s in classroom.students.all()]
            result.append(classroom_data)
        return jsonify({'classrooms': result, 'count': len(result)})

class ExportFeedback(Resource):
    @token_required()
    def get(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        feedback_items = Feedback.query.all()
        return jsonify({'feedback': [f.read() for f in feedback_items], 'count': len(feedback_items)})

class ExportStudy(Resource):
    @token_required()
    def get(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        study_records = Study.query.all()
        result = []
        for study in study_records:
            study_data = study.to_dict()
            if study.user_id:
                user = User.query.get(study.user_id)
                if user:
                    study_data['userUid'] = user.uid
            result.append(study_data)
        return jsonify({'study': result, 'count': len(result)})

class ExportPersonas(Resource):
    @token_required()
    def get(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403

        # Always use pagination to prevent timeouts
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        pagination = Persona.query.paginate(page=page, per_page=per_page, error_out=False)

        return jsonify({
            'personas': [p.read() for p in pagination.items],
            'count': len(pagination.items),
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'has_next': pagination.has_next,
            'has_prev': pagination.has_prev
        })

class ExportUserPersonas(Resource):
    @token_required()
    def get(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403

        # Always use pagination to prevent timeouts
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        pagination = UserPersona.query.paginate(page=page, per_page=per_page, error_out=False)

        result = []
        for up in pagination.items:
            result.append({
                'userUid': up.user.uid if up.user else None,
                'personaAlias': up.persona.alias if up.persona else None,
                'weight': up.weight,
                'selectedAt': up.selected_at.isoformat() if up.selected_at else None
            })

        return jsonify({
            'user_personas': result,
            'count': len(result),
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'has_next': pagination.has_next,
            'has_prev': pagination.has_prev
        })


def _export_events(model, key):
    """Paginated export shared by the two leaderboard event tables."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    pagination = model.query.order_by(model.id).paginate(page=page, per_page=per_page, error_out=False)

    result = []
    for event in pagination.items:
        result.append({
            'id':        event.id,
            'userUid':   event.user.uid if event.user else None,
            'payload':   event._payload or {},
            'timestamp': event._timestamp.isoformat() if event._timestamp else None,
        })

    return jsonify({
        key: result,
        'count': len(result),
        'total': pagination.total,
        'page': page,
        'per_page': per_page,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev
    })


class ExportLeaderboard(Resource):
    """Export the SCORE_COUNTER leaderboard events ('leaderboard' table)."""
    @token_required()
    def get(self):
        if g.current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        return _export_events(ScoreCounterEvent, 'leaderboard')


class ExportElementaryLeaderboard(Resource):
    """Export the elementary leaderboard events."""
    @token_required()
    def get(self):
        if g.current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        return _export_events(ElementaryLeaderboardEvent, 'elementary_leaderboard')


class ExportSkillSnapshots(Resource):
    """Export skill snapshots."""
    @token_required()
    def get(self):
        if g.current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403

        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        pagination = SkillSnapshot.query.order_by(SkillSnapshot.id).paginate(
            page=page, per_page=per_page, error_out=False
        )

        result = []
        for snapshot in pagination.items:
            data = snapshot.read()
            data['userUid'] = snapshot.user.uid if snapshot.user else None
            result.append(data)

        return jsonify({
            'skill_snapshots': result,
            'count': len(result),
            'total': pagination.total,
            'page': page,
            'per_page': per_page,
            'has_next': pagination.has_next,
            'has_prev': pagination.has_prev
        })


# ============ Chunked Import Endpoints ============
# These allow importing data one type at a time to avoid timeouts

class ImportSections(Resource):
    """Import sections only"""
    @token_required()
    def post(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        
        data = request.get_json()
        sections_data = data.get('sections', [])
        
        try:
            result = ImportAllData()._import_sections(sections_data)
            db.session.commit()
            return jsonify({'sections': result, 'message': 'Sections import complete'})
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


class ImportUsers(Resource):
    """Import users only"""
    @token_required()
    def post(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        
        data = request.get_json()
        users_data = data.get('users', [])
        
        try:
            result = ImportAllData()._import_users(users_data)
            db.session.commit()
            return jsonify({'users': result, 'message': 'Users import complete'})
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


class ImportTopics(Resource):
    """Import topics only"""
    @token_required()
    def post(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        
        data = request.get_json()
        topics_data = data.get('topics', [])
        
        try:
            result = ImportAllData()._import_topics(topics_data)
            db.session.commit()
            return jsonify({'topics': result, 'message': 'Topics import complete'})
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


class ImportMicroblogs(Resource):
    """Import microblogs only"""
    @token_required()
    def post(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        
        data = request.get_json()
        microblogs_data = data.get('microblogs', [])
        
        try:
            result = ImportAllData()._import_microblogs(microblogs_data)
            db.session.commit()
            return jsonify({'microblogs': result, 'message': 'Microblogs import complete'})
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


class ImportPosts(Resource):
    """Import posts only"""
    @token_required()
    def post(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        
        data = request.get_json()
        posts_data = data.get('posts', [])
        
        try:
            result = ImportAllData()._import_posts(posts_data)
            db.session.commit()
            return jsonify({'posts': result, 'message': 'Posts import complete'})
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


class ImportClassrooms(Resource):
    """Import classrooms only"""
    @token_required()
    def post(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        
        data = request.get_json()
        classrooms_data = data.get('classrooms', [])
        
        try:
            result = ImportAllData()._import_classrooms(classrooms_data)
            db.session.commit()
            return jsonify({'classrooms': result, 'message': 'Classrooms import complete'})
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


class ImportFeedback(Resource):
    """Import feedback only"""
    @token_required()
    def post(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        
        data = request.get_json()
        feedback_data = data.get('feedback', [])
        
        try:
            result = ImportAllData()._import_feedback(feedback_data)
            db.session.commit()
            return jsonify({'feedback': result, 'message': 'Feedback import complete'})
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


class ImportStudy(Resource):
    """Import study records only"""
    @token_required()
    def post(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        
        data = request.get_json()
        study_data = data.get('study', [])
        
        try:
            result = ImportAllData()._import_study(study_data)
            db.session.commit()
            return jsonify({'study': result, 'message': 'Study import complete'})
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


class ImportPersonas(Resource):
    """Import personas only"""
    @token_required()
    def post(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        
        data = request.get_json()
        personas_data = data.get('personas', [])
        
        try:
            result = ImportAllData()._import_personas(personas_data)
            db.session.commit()
            return jsonify({'personas': result, 'message': 'Personas import complete'})
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


class ImportUserPersonas(Resource):
    """Import user-persona associations only"""
    @token_required()
    def post(self):
        current_user = g.current_user
        if current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403
        
        data = request.get_json()
        user_personas_data = data.get('user_personas', [])
        
        try:
            result = ImportAllData()._import_user_personas(user_personas_data)
            db.session.commit()
            return jsonify({'user_personas': result, 'message': 'User personas import complete'})
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


class ImportLeaderboard(Resource):
    """Import SCORE_COUNTER leaderboard events only"""
    @token_required()
    def post(self):
        if g.current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403

        data = request.get_json()
        try:
            result = ImportAllData()._import_leaderboard(data.get('leaderboard', []))
            db.session.commit()
            return jsonify({'leaderboard': result, 'message': 'Leaderboard import complete'})
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


class ImportElementaryLeaderboard(Resource):
    """Import elementary leaderboard events only"""
    @token_required()
    def post(self):
        if g.current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403

        data = request.get_json()
        try:
            result = ImportAllData()._import_elementary_leaderboard(
                data.get('elementary_leaderboard', [])
            )
            db.session.commit()
            return jsonify({
                'elementary_leaderboard': result,
                'message': 'Elementary leaderboard import complete',
            })
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


class ImportSkillSnapshots(Resource):
    """Import skill snapshots only"""
    @token_required()
    def post(self):
        if g.current_user.role != 'Admin':
            return {'message': 'Admin privileges required'}, 403

        data = request.get_json()
        try:
            result = ImportAllData()._import_skill_snapshots(data.get('skill_snapshots', []))
            db.session.commit()
            return jsonify({'skill_snapshots': result, 'message': 'Skill snapshots import complete'})
        except Exception as e:
            db.session.rollback()
            return {'message': f'Import failed: {str(e)}'}, 500


# Register endpoints
api.add_resource(ExportAllData, '/all')
api.add_resource(ImportAllData, '/import')
api.add_resource(ExportCounts, '/counts')

# Chunked export endpoints
api.add_resource(ExportSections, '/sections')
api.add_resource(ExportUsers, '/users')
api.add_resource(ExportTopics, '/topics')
api.add_resource(ExportMicroblogs, '/microblogs')
api.add_resource(ExportPosts, '/posts')
api.add_resource(ExportClassrooms, '/classrooms')
api.add_resource(ExportFeedback, '/feedback')
api.add_resource(ExportStudy, '/study')
api.add_resource(ExportPersonas, '/personas')
api.add_resource(ExportUserPersonas, '/user_personas')
api.add_resource(ExportLeaderboard, '/leaderboard')
api.add_resource(ExportElementaryLeaderboard, '/elementary_leaderboard')
api.add_resource(ExportSkillSnapshots, '/skill_snapshots')

# Chunked import endpoints (POST to same paths as export)
api.add_resource(ImportSections, '/import/sections')
api.add_resource(ImportUsers, '/import/users')
api.add_resource(ImportTopics, '/import/topics')
api.add_resource(ImportMicroblogs, '/import/microblogs')
api.add_resource(ImportPosts, '/import/posts')
api.add_resource(ImportClassrooms, '/import/classrooms')
api.add_resource(ImportFeedback, '/import/feedback')
api.add_resource(ImportStudy, '/import/study')
api.add_resource(ImportPersonas, '/import/personas')
api.add_resource(ImportUserPersonas, '/import/user_personas')
api.add_resource(ImportLeaderboard, '/import/leaderboard')
api.add_resource(ImportElementaryLeaderboard, '/import/elementary_leaderboard')
api.add_resource(ImportSkillSnapshots, '/import/skill_snapshots')
