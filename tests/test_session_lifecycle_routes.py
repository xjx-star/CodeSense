from datetime import datetime, timedelta
import json

import pytest

from app import create_app
from config import TestingConfig
from models import Assignment, AssignmentThinkingPreset, Class, ThinkingSession, ThinkingStageLog, User, db


@pytest.fixture
def lifecycle_routes_context(tmp_path, monkeypatch):
    database_path = tmp_path / "session-lifecycle-routes.db"
    monkeypatch.setattr(TestingConfig, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{database_path}")
    app = create_app("testing")
    now = datetime.utcnow()

    with app.app_context():
        db.create_all()
        author = User(
            student_id="author-teacher",
            username="author-teacher",
            usertype="教师",
            full_name="作业教师",
        )
        author.password = "password"
        managed = User(
            student_id="managed-teacher",
            username="managed-teacher",
            usertype="教师",
            full_name="班级教师",
        )
        managed.password = "password"
        outsider = User(
            student_id="outsider-teacher",
            username="outsider-teacher",
            usertype="教师",
            full_name="无关教师",
        )
        outsider.password = "password"
        student = User(
            student_id="lifecycle-student",
            username="lifecycle-student",
            usertype="学生",
            class_name="连续性班",
            full_name="连续性学生",
        )
        student.password = "password"
        classroom = Class(name="连续性班", teacher_id=managed.student_id)
        db.session.add_all([author, managed, outsider, student, classroom])
        db.session.flush()
        student.class_id = classroom.id
        assignment = Assignment(
            title="会话连续性作业",
            description="测试作业",
            creator_id=author.student_id,
            target_classes=classroom.name,
        )
        db.session.add(assignment)
        db.session.flush()
        db.session.add(AssignmentThinkingPreset(
            assignment_id=assignment.id,
            reference_code="int main() { return 0; }",
            key_steps=json.dumps(["描述输入和输出"], ensure_ascii=False),
            code_blocks=json.dumps([{"id": "one", "code": "return 0;"}], ensure_ascii=False),
            noise_blocks="[]",
            quiz_steps=json.dumps([{
                "step_id": 1,
                "type": "fill",
                "question": "填写返回值",
                "correct_answer": "0",
            }], ensure_ascii=False),
            difficulty_config=json.dumps({"feynman_rounds": 2}, ensure_ascii=False),
            status="ready",
        ))
        active = ThinkingSession(
            student_id=student.student_id,
            assignment_id=assignment.id,
            current_stage=2,
            stage1_description="先处理输入",
            started_at=now - timedelta(minutes=2),
            status="in_progress",
        )
        idle = ThinkingSession(
            student_id=student.student_id,
            assignment_id=assignment.id,
            current_stage=1,
            started_at=now - timedelta(hours=2),
            status="in_progress",
        )
        completed = ThinkingSession(
            student_id=student.student_id,
            assignment_id=assignment.id,
            current_stage=3,
            stage2_completed=True,
            stage3_completed=True,
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(minutes=50),
            status="completed",
            total_time_seconds=600,
        )
        db.session.add_all([active, idle, completed])
        db.session.flush()
        db.session.add_all([
            ThinkingStageLog(
                session_id=active.id,
                stage=2,
                event_type="block_move",
                role="student",
                content="",
                created_at=now - timedelta(minutes=2),
            ),
            ThinkingStageLog(
                session_id=idle.id,
                stage=1,
                event_type="session_start",
                role="student",
                content="",
                created_at=now - timedelta(hours=2),
            ),
        ])
        db.session.commit()
        ids = {
            "assignment": assignment.id,
            "active": active.id,
            "idle": idle.id,
            "completed": completed.id,
        }

    yield app, ids

    with app.app_context():
        db.session.remove()
        db.drop_all()


def _login(client, username):
    response = client.post("/login", data={"username": username, "password": "password"})
    assert response.status_code in {302, 303}


def test_student_status_endpoint_returns_explainable_resume_projection(lifecycle_routes_context):
    app, ids = lifecycle_routes_context
    client = app.test_client()
    _login(client, "lifecycle-student")

    response = client.get(f"/thinking/api/session/{ids['active']}/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["session"]["status"] == "active"
    assert payload["session"]["progress_percent"] == 33
    assert payload["session"]["is_resumable"] is True
    assert payload["session"]["elapsed_source"] == "server_clock"


def test_status_endpoint_hides_missing_and_unrelated_sessions(lifecycle_routes_context):
    app, ids = lifecycle_routes_context
    client = app.test_client()
    _login(client, "outsider-teacher")

    unrelated = client.get(f"/thinking/api/session/{ids['active']}/status")
    missing = client.get("/thinking/api/session/999999/status")

    assert unrelated.status_code == 403
    assert missing.status_code == 403
    assert "session" not in unrelated.get_json()
    assert "session" not in missing.get_json()


def test_managed_teacher_can_read_status_and_assignment_list_has_lifecycle_filter(
    lifecycle_routes_context,
):
    app, ids = lifecycle_routes_context
    client = app.test_client()
    _login(client, "managed-teacher")

    status_response = client.get(f"/thinking/api/session/{ids['active']}/status")
    list_response = client.get(
        f"/thinking/api/assignment/{ids['assignment']}/sessions?lifecycle_status=idle"
    )

    assert status_response.status_code == 200
    assert list_response.status_code == 200
    rows = list_response.get_json()["sessions"]
    assert [row["id"] for row in rows] == [ids["idle"]]
    assert rows[0]["lifecycle_status"] == "idle"
    assert rows[0]["progress_percent"] == 0


def test_start_session_resume_returns_the_same_lifecycle_projection(lifecycle_routes_context):
    app, ids = lifecycle_routes_context
    client = app.test_client()
    _login(client, "lifecycle-student")

    response = client.post(
        "/thinking/api/start_session",
        json={"assignment_id": ids["assignment"]},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["resumed"] is True
    assert payload["elapsed_seconds"] == payload["session_lifecycle"]["elapsed_seconds"]
    assert payload["session_lifecycle"]["id"] == ids["active"]


def test_teacher_session_overview_page_has_empty_safe_read_only_contract(lifecycle_routes_context):
    app, ids = lifecycle_routes_context
    client = app.test_client()
    _login(client, "managed-teacher")

    response = client.get(f"/thinking/assignment/{ids['assignment']}/sessions/view")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "学习会话概览" in body
    assert "状态来自会话活动日志" in body
    assert "连续性学生" in body
    assert "active" in body


def test_unrelated_teacher_cannot_open_session_overview_page(lifecycle_routes_context):
    app, ids = lifecycle_routes_context
    client = app.test_client()
    _login(client, "outsider-teacher")

    response = client.get(f"/thinking/assignment/{ids['assignment']}/sessions/view")

    assert response.status_code == 403


def test_student_home_exposes_recent_session_context(lifecycle_routes_context):
    app, ids = lifecycle_routes_context
    client = app.test_client()
    _login(client, "lifecycle-student")

    response = client.get("/home")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "继续学习" in body
    assert "会话连续性作业" in body
    assert "继续当前阶段" in body or "查看学习记录" in body
