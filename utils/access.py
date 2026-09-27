"""Shared authorization and data-scope checks.

The application has several generations of routes.  Keeping the scope rules
in one small module prevents a legacy route from accidentally treating a
teacher as an administrator or from using a substring match for a class name.
"""

from __future__ import annotations

from flask_login import current_user
from sqlalchemy import and_, false, or_

from models import Assignment, Class, User


def _actor(actor=None):
    return actor if actor is not None else current_user


def is_admin(actor=None) -> bool:
    actor = _actor(actor)
    return bool(getattr(actor, "is_admin", False) or getattr(actor, "usertype", None) == "管理员")


def is_teacher(actor=None) -> bool:
    actor = _actor(actor)
    return bool(getattr(actor, "is_teacher", False) or getattr(actor, "usertype", None) == "教师")


def is_student(actor=None) -> bool:
    actor = _actor(actor)
    return getattr(actor, "usertype", None) == "学生"


def managed_classes(actor=None):
    """Return the classes an actor may manage or inspect."""

    actor = _actor(actor)
    if is_admin(actor):
        return Class.query.order_by(Class.name.asc()).all()
    if not is_teacher(actor):
        return []

    relationship = getattr(actor, "managed_classes", None)
    if relationship is None:
        return []
    try:
        return relationship.order_by(Class.name.asc()).all()
    except AttributeError:
        return relationship.all()


def managed_class_ids(actor=None) -> set[int]:
    return {int(classroom.id) for classroom in managed_classes(actor) if classroom.id is not None}


def managed_class_names(actor=None) -> set[str]:
    return {
        str(classroom.name).strip()
        for classroom in managed_classes(actor)
        if str(getattr(classroom, "name", "") or "").strip()
    }


def class_student_filter(classroom):
    """Match students in a class across the FK and legacy name fields.

    ``class_id`` wins for migrated records.  The old name field is accepted
    only when the FK is empty, so a stale name cannot widen a class boundary.
    """

    if classroom is None:
        return false()
    return or_(
        User.class_id == classroom.id,
        and_(User.class_id.is_(None), User.class_name == classroom.name),
    )


def authoritative_class_name(actor=None) -> str:
    """Return the actor's current class name from the FK when available.

    ``class_name`` is retained for pre-FK accounts, but must not override a
    populated ``class_id``.  Keeping this lookup beside the access checks
    prevents list pages and permission checks from disagreeing about the
    student's current class.
    """

    actor = _actor(actor)
    class_id = getattr(actor, "class_id", None)
    if class_id is not None:
        classroom = Class.query.get(class_id)
        return str(getattr(classroom, "name", "") or "").strip()
    return str(getattr(actor, "class_name", "") or "").strip()


def can_access_class(classroom, actor=None) -> bool:
    actor = _actor(actor)
    if classroom is None or not getattr(actor, "is_authenticated", False):
        return False
    if is_admin(actor):
        return True
    return is_teacher(actor) and classroom.teacher_id == getattr(actor, "student_id", None)


def can_access_student(student, actor=None) -> bool:
    """Check whether an actor may inspect a student's private learning data."""

    actor = _actor(actor)
    if student is None or not getattr(actor, "is_authenticated", False):
        return False

    actor_id = getattr(actor, "student_id", None)
    if actor_id and actor_id == getattr(student, "student_id", None):
        return True
    if is_admin(actor):
        return True
    if not is_teacher(actor) or getattr(student, "usertype", None) != "学生":
        return False

    classes = managed_classes(actor)
    managed_ids = {classroom.id for classroom in classes}
    managed_names = {classroom.name for classroom in classes}

    # ``class_id`` is the authoritative relationship for newly imported
    # rosters, while ``class_name`` is only a compatibility fallback for
    # accounts that predate the foreign key.  A stale name must not expand a
    # teacher's visibility after a student has been moved to another class.
    student_class_id = getattr(student, "class_id", None)
    if student_class_id is not None:
        return student_class_id in managed_ids
    return bool(getattr(student, "class_name", None) in managed_names)


def assignment_target_class_names(assignment) -> set[str]:
    if assignment is None:
        return set()
    getter = getattr(assignment, "get_target_class_list", None)
    if callable(getter):
        values = getter()
    else:
        values = str(getattr(assignment, "target_classes", "") or "").split(",")
    return {str(value).strip() for value in values if str(value).strip()}


def assignment_target_class_filter(class_name):
    """Build an exact filter for the legacy comma-separated class field.

    ``target_classes`` predates the class relationship and is still stored as
    text.  A normal ``LIKE %name%`` is unsafe for scope checks because class
    names such as ``软工24-1`` and ``软工24-10`` overlap.
    """

    class_name = str(class_name or '').strip()
    if not class_name:
        return Assignment.target_classes.is_(None) | (Assignment.target_classes == '')

    escaped = (
        class_name
        .replace('\\', '\\\\')
        .replace('%', '\\%')
        .replace('_', '\\_')
    )
    return or_(
        Assignment.target_classes == class_name,
        Assignment.target_classes.like(f'{escaped},%', escape='\\'),
        Assignment.target_classes.like(f'%,{escaped},%', escape='\\'),
        Assignment.target_classes.like(f'%,{escaped}', escape='\\'),
    )


def can_access_assignment(assignment, actor=None, *, allow_unassigned=True) -> bool:
    """Check assignment visibility for the current role.

    Empty target lists are retained only for administrator-created legacy
    assignments (and the actor's own legacy fixture records); a teacher's
    unassigned draft must not become visible to every student.  Once a
    teacher assigns classes, students must match a complete class name rather
    than a substring of it.
    """

    actor = _actor(actor)
    if assignment is None or not getattr(actor, "is_authenticated", False):
        return False
    if is_admin(actor):
        return True

    actor_id = getattr(actor, "student_id", None)
    target_names = assignment_target_class_names(assignment)

    if is_teacher(actor):
        return (
            getattr(assignment, "creator_id", None) == actor_id
            or bool(target_names & managed_class_names(actor))
        )

    if not is_student(actor):
        return False
    if not target_names:
        return allow_unassigned and getattr(assignment, "creator_id", None) in {
            None,
            actor_id,
        }

    actor_class_id = getattr(actor, "class_id", None)
    if actor_class_id is not None:
        classroom = Class.query.get(actor_class_id)
        class_name = str(getattr(classroom, "name", "") or "").strip()
    else:
        class_name = str(getattr(actor, "class_name", "") or "").strip()
    return bool(class_name and class_name in target_names)


def can_manage_assignment(assignment, actor=None) -> bool:
    actor = _actor(actor)
    if assignment is None or not getattr(actor, "is_authenticated", False):
        return False
    return is_admin(actor) or (
        is_teacher(actor)
        and getattr(assignment, "creator_id", None) == getattr(actor, "student_id", None)
    )


def can_access_submission(submission, actor=None) -> bool:
    """Check private submission access for students, teachers and admins."""

    actor = _actor(actor)
    if submission is None or not getattr(actor, "is_authenticated", False):
        return False
    actor_id = getattr(actor, "student_id", None)
    if actor_id == getattr(submission, "student_id", None):
        return True
    if is_admin(actor):
        return True
    if not is_teacher(actor):
        return False

    student = User.query.get(getattr(submission, "student_id", None))
    if can_access_student(student, actor):
        return True

    # A teacher who created the assignment may review its submissions even if
    # the receiving class was later rebound to another teacher.
    assignment = Assignment.query.get(getattr(submission, "assignment_id", None))
    return bool(assignment and assignment.creator_id == actor_id)


def can_access_thinking_session(thinking_session, actor=None) -> bool:
    actor = _actor(actor)
    if thinking_session is None or not getattr(actor, "is_authenticated", False):
        return False
    if getattr(thinking_session, "student_id", None) == getattr(actor, "student_id", None):
        return True
    if is_admin(actor):
        return True
    if not is_teacher(actor):
        return False
    assignment = Assignment.query.get(getattr(thinking_session, "assignment_id", None))
    if assignment and can_access_assignment(assignment, actor):
        return True
    student = User.query.get(getattr(thinking_session, "student_id", None))
    return can_access_student(student, actor)
