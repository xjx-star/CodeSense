import json
from datetime import datetime, timedelta

import pytest

from app import create_app
from config import TestingConfig as _TestingConfig
from models import (
    AbilityTrend,
    Assignment,
    Class,
    Submission,
    TeacherAISuggestion,
    ThinkingSession,
    User,
    db,
)
from services.action_center import build_action_center
from services.feedback import create_feedback_record, save_feedback
from services.notifications import create_notification
from services.submission_reviews import create_review_request


@pytest.fixture
def action_center_context(tmp_path, monkeypatch):
    database_path = tmp_path / "action-center.db"
    monkeypatch.setattr(_TestingConfig, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{database_path}")
    app = create_app("testing")
    now = datetime.utcnow()

    with app.app_context():
        db.create_all()
        student = User(
            student_id="action-student",
            username="action-student",
            usertype="学生",
            full_name="行动中心学生",
            email="student-private@example.test",
        )
        teacher = User(
            student_id="action-teacher",
            username="action-teacher",
            usertype="教师",
            full_name="行动中心教师",
        )
        outsider_teacher = User(
            student_id="other-teacher",
            username="other-teacher",
            usertype="教师",
            full_name="其他教师",
        )
        admin = User(
            student_id="action-admin",
            username="action-admin",
            usertype="管理员",
            full_name="行动中心管理员",
        )
        for user in (student, teacher, outsider_teacher, admin):
            user.password = "password"

        managed_class = Class(name="行动中心班", teacher_id=teacher.student_id)
        outsider_class = Class(name="其他教师班", teacher_id=outsider_teacher.student_id)
        db.session.add_all([
            student,
            teacher,
            outsider_teacher,
            admin,
            managed_class,
            outsider_class,
        ])
        db.session.flush()
        student.class_id = managed_class.id

        assignment = Assignment(
            title="行动中心作业",
            description="只用于聚合测试",
            creator_id=teacher.student_id,
            target_classes=managed_class.name,
        )
        outsider_assignment = Assignment(
            title="其他教师作业",
            description="不应进入当前教师队列",
            creator_id=outsider_teacher.student_id,
            target_classes=outsider_class.name,
        )
        db.session.add_all([assignment, outsider_assignment])
        db.session.flush()

        pending_submission = Submission(
            student_id=student.student_id,
            assignment_id=assignment.id,
            code="SECRET_STUDENT_CODE",
            status="pending",
            submitted_at=now - timedelta(minutes=3),
        )
        review_submission = Submission(
            student_id=student.student_id,
            assignment_id=assignment.id,
            code="PRIVATE_REVIEW_CODE",
            status="evaluated",
            submitted_at=now - timedelta(minutes=8),
        )
        outsider_submission = Submission(
            student_id=student.student_id,
            assignment_id=outsider_assignment.id,
            code="OUTSIDER_CODE",
            status="pending",
            submitted_at=now - timedelta(minutes=1),
        )
        db.session.add_all([pending_submission, review_submission, outsider_submission])
        db.session.flush()
        session = ThinkingSession(
            student_id=student.student_id,
            assignment_id=assignment.id,
            current_stage=2,
            stage1_description="private learning content",
            started_at=now - timedelta(minutes=2),
            status="in_progress",
        )
        db.session.add(session)
        db.session.add_all([
            TeacherAISuggestion(
                teacher_id=teacher.student_id,
                class_id=managed_class.id,
                status="failed",
                suggestion_markdown="PRIVATE_TEACHER_AI_OUTPUT",
            ),
            TeacherAISuggestion(
                teacher_id=outsider_teacher.student_id,
                class_id=outsider_class.id,
                status="failed",
                suggestion_markdown="OUTSIDER_TEACHER_AI_OUTPUT",
            ),
            AbilityTrend(student_id=student.student_id, status="failed"),
        ])
        db.session.commit()

        create_review_request(
            review_submission,
            student.student_id,
            "PRIVATE_REVIEW_BODY",
        )
        create_notification(
            student.student_id,
            kind="submission",
            title="提交状态已更新",
            message="你的提交需要查看。",
            url="/submissions",
            idempotency_key="action-center-student-notification",
        )
        feedback = create_feedback_record(
            {
                "category": "bug",
                "subject": "后台反馈主题",
                "message": "后台反馈的私密正文不应出现在行动中心。",
                "reproduction_steps": "只用于测试",
                "page_context": "/feedback",
                "contact_email": "feedback-private@example.test",
            },
            request_context={"request_id": "action-center-test", "endpoint": "/feedback", "method": "POST"},
        )
        save_feedback(feedback, user_id=student.student_id)

        yield app, {
            "student": student.student_id,
            "teacher": teacher.student_id,
            "admin": admin.student_id,
            "outsider_teacher": outsider_teacher.student_id,
        }

    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _login(client, username):
    response = client.post("/login", data={"username": username, "password": "password"})
    assert response.status_code in {302, 303}


def test_student_contract_is_bounded_owned_and_content_free(action_center_context):
    app, ids = action_center_context
    with app.app_context():
        payload = build_action_center(db.session.get(User, ids["student"]), limit=200)

    kinds = {item["kind"] for item in payload["items"]}
    assert payload["schema_version"] == 1
    assert payload["role"] == "student"
    assert payload["data_scope"] == "actor-owned"
    assert {"submission", "review", "session", "notification"}.issubset(kinds)
    assert len(payload["items"]) <= 50
    assert payload["counts"]["total"] >= len(payload["items"])
    assert all(item["href"].startswith("/") for item in payload["items"])
    assert all(not item["id"].rsplit(":", 1)[-1].isdigit() for item in payload["items"])
    serialized = json.dumps(payload, ensure_ascii=False)
    for private_value in (
        "SECRET_STUDENT_CODE",
        "PRIVATE_REVIEW_CODE",
        "PRIVATE_REVIEW_BODY",
        "student-private@example.test",
        "feedback-private@example.test",
    ):
        assert private_value not in serialized


def test_teacher_and_admin_sources_are_role_scoped(action_center_context):
    app, ids = action_center_context
    with app.app_context():
        teacher_payload = build_action_center(db.session.get(User, ids["teacher"]), limit=50)
        trend = db.session.get(AbilityTrend, 1)
        trend.status = "processing"
        db.session.commit()
        admin_payload = build_action_center(db.session.get(User, ids["admin"]), limit=50)

    teacher_kinds = {item["kind"] for item in teacher_payload["items"]}
    admin_kinds = {item["kind"] for item in admin_payload["items"]}
    assert teacher_payload["role"] == "teacher"
    assert {"review", "teacher_ai"}.issubset(teacher_kinds)
    assert "admin_feedback" not in teacher_kinds
    teacher_json = json.dumps(teacher_payload, ensure_ascii=False)
    assert "其他教师作业" not in teacher_json
    assert "OUTSIDER_TEACHER_AI_OUTPUT" not in teacher_json
    assert {"feedback", "ability"}.issubset(admin_kinds)
    assert admin_payload["data_scope"] == "system-queue"
    assert any(item["status"] == "processing" for item in admin_payload["items"] if item["kind"] == "ability")
    assert "行动中心学生" not in json.dumps(admin_payload, ensure_ascii=False)


def test_priority_filter_limit_and_degraded_source_contract(action_center_context, monkeypatch):
    app, ids = action_center_context
    import services.action_center as action_center

    with app.app_context():
        urgent = build_action_center(db.session.get(User, ids["student"]), priority="urgent", limit=999)
        info = build_action_center(db.session.get(User, ids["student"]), priority="info", limit=999)
        fallback = build_action_center(db.session.get(User, ids["student"]), priority="not-valid")

        def broken_source(_actor, **_kwargs):
            raise RuntimeError("SECRET_INTERNAL_ERROR")

        monkeypatch.setattr(action_center, "_read_student_submissions", broken_source)
        degraded = build_action_center(db.session.get(User, ids["student"]))

    assert urgent["items"]
    assert all(item["priority"] == "urgent" for item in urgent["items"])
    assert all(item["priority"] == "info" for item in info["items"])
    assert fallback["counts"]["total"] >= len(fallback["items"])
    assert "submissions" in degraded["degraded_sources"]
    assert "SECRET_INTERNAL_ERROR" not in json.dumps(degraded, ensure_ascii=False)


def test_action_center_routes_require_login_and_return_no_store_json(action_center_context):
    app, ids = action_center_context
    client = app.test_client()
    assert client.get("/action-center").status_code in {302, 303}
    assert client.get("/api/action-center").status_code in {302, 303}

    _login(client, ids["student"])
    page = client.get("/action-center")
    response = client.get("/api/action-center?priority=urgent&limit=999")

    assert page.status_code == 200
    assert "行动中心" in page.get_data(as_text=True)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["schema_version"] == 1
    assert all(item["priority"] == "urgent" for item in response.get_json()["items"])


def test_action_center_page_has_accessible_queue_contract(action_center_context):
    app, ids = action_center_context
    client = app.test_client()
    _login(client, ids["student"])
    html = client.get("/action-center").get_data(as_text=True)

    assert 'aria-live="polite"' in html
    assert 'aria-labelledby="action-center-title"' in html
    assert "action-center.css" in html
    assert "学习队列" in html
    assert "SECRET_STUDENT_CODE" not in html


def test_role_pages_render_their_scoped_queue_sources(action_center_context):
    app, ids = action_center_context

    teacher_client = app.test_client()
    _login(teacher_client, ids["teacher"])
    teacher_html = teacher_client.get("/action-center").get_data(as_text=True)
    assert "班级建议：行动中心班" in teacher_html
    assert "其他教师作业" not in teacher_html
    assert 'class="action-center-count notification-count"' in teacher_html

    teacher_client.post("/logout")
    _login(teacher_client, ids["admin"])
    admin_html = teacher_client.get("/action-center").get_data(as_text=True)
    assert "反馈：后台反馈主题" in admin_html
    assert "能力分析：系统队列" in admin_html
    assert "行动中心学生" not in admin_html
    assert "PRIVATE_REVIEW_BODY" not in admin_html
