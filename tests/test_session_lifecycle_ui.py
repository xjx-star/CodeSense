from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARENA_TEMPLATE = (PROJECT_ROOT / "templates" / "thinking" / "arena.html").read_text(encoding="utf-8")
THINKING_JS = (PROJECT_ROOT / "static" / "js" / "thinking.js").read_text(encoding="utf-8")
THINKING_CSS = (PROJECT_ROOT / "static" / "css" / "thinking.css").read_text(encoding="utf-8")
STUDENT_HOME = (PROJECT_ROOT / "templates" / "student_home.html").read_text(encoding="utf-8")
TEACHER_ASSIGNMENTS = (PROJECT_ROOT / "templates" / "teacher_assignments.html").read_text(encoding="utf-8")


def test_arena_exposes_accessible_session_lifecycle_status_strip():
    assert 'id="session-lifecycle-status"' in ARENA_TEMPLATE
    assert 'id="session-lifecycle-meta"' in ARENA_TEMPLATE
    assert 'id="session-next-action"' in ARENA_TEMPLATE
    assert 'id="session-status-refresh"' in ARENA_TEMPLATE
    assert 'role="status"' in ARENA_TEMPLATE
    assert 'aria-live="polite"' in ARENA_TEMPLATE
    assert 'data-session-status' in ARENA_TEMPLATE


def test_lifecycle_sync_is_visibility_aware_and_does_not_reload_or_redirect():
    assert "function refreshSessionLifecycle" in THINKING_JS
    assert "/thinking/api/session/${state.sessionId}/status" in THINKING_JS
    assert "document.addEventListener('visibilitychange'" in THINKING_JS
    assert "document.visibilityState === 'visible'" in THINKING_JS
    sync_block = THINKING_JS[THINKING_JS.index("function refreshSessionLifecycle"):THINKING_JS.index("function init()")]
    assert "location.reload" not in sync_block
    assert "window.location" not in sync_block


def test_lifecycle_sync_updates_server_elapsed_and_failure_message_without_touching_sse():
    assert "function applySessionLifecycle" in THINKING_JS
    assert "session_lifecycle" in THINKING_JS
    assert "状态已同步" in THINKING_JS
    assert "状态同步失败，当前输入仍然保留" in THINKING_JS
    assert "visibilitychange" in THINKING_JS
    assert "visibilitychange" not in THINKING_JS[THINKING_JS.index("function fetchAIStream"):]


def test_lifecycle_status_strip_has_narrow_screen_safe_styles():
    assert ".session-lifecycle" in THINKING_CSS
    assert "min-width: 0" in THINKING_CSS
    assert "overflow-wrap: anywhere" in THINKING_CSS


def test_student_home_contains_continue_learning_empty_safe_surface():
    assert 'id="recent-learning-title"' in STUDENT_HOME
    assert "继续学习" in STUDENT_HOME
    assert "还没有学习会话" in STUDENT_HOME
    assert "继续当前阶段" in STUDENT_HOME


def test_teacher_assignment_list_links_to_read_only_session_overview():
    assert "thinking.assignment_sessions_view" in TEACHER_ASSIGNMENTS
    assert "学习会话" in TEACHER_ASSIGNMENTS
