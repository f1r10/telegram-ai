"""Centralized Phase 2 access policy."""

from office_assistant.infrastructure.database.models import User, UserRole, UserStatus


def is_authenticated(user: User | None) -> bool:
    return user is not None and user.status == UserStatus.ACTIVE


def is_active(user: User | None) -> bool:
    return is_authenticated(user)


def is_admin(user: User | None) -> bool:
    return is_active(user) and user is not None and user.role == UserRole.ADMIN


def can_manage_users(user: User | None) -> bool:
    return is_admin(user)
