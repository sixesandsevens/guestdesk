"""Checkbox-based permission system for admin areas.

The role field stays as broad account identity (viewer/editor/admin); access
to each admin area is governed by explicit per-user permission grants.
Admin-role users (and the one-password admin session) pass every check.
"""

# GuestDesk
# Copyright (c) 2025 Chris Tanton
# SPDX-License-Identifier: LicenseRef-GDCL-1.1
from __future__ import annotations

from functools import wraps

from flask import abort, current_app, g, session

from .models import UserPermission

# (group label, [(key, checkbox label), ...]) — drives the user edit UI
PERMISSION_GROUPS = [
    ('Users', [
        ('admin.users.manage', 'Manage users and permissions'),
    ]),
    ('Services', [
        ('services.view', 'View services admin'),
        ('services.edit', 'Edit services, schedules, and announcements'),
    ]),
    ('Displays', [
        ('displays.view', 'View display admin'),
        ('displays.edit', 'Edit displays and slideshows'),
    ]),
    ('Submissions', [
        ('submissions.view', 'View form submissions'),
        ('submissions.manage', 'Manage form submissions'),
    ]),
    ('Grievances', [
        ('grievances.view', 'View grievance tracker'),
        ('grievances.create', 'Create staff-entered grievances'),
        ('grievances.review', 'Review/update grievance cases'),
        ('grievances.assign', 'Assign grievance reviewers'),
        ('grievances.close', 'Close/reopen/archive grievance cases'),
        ('grievances.attach', 'Upload grievance attachments'),
    ]),
    ('PDF Forms', [
        ('pdf_forms.view', 'View PDF form editor'),
        ('pdf_forms.edit', 'Edit PDF form templates'),
    ]),
    ('Settings', [
        ('settings.grievance_email.edit', 'Edit notification email recipients'),
        ('settings.site.edit', 'Edit site/app settings'),
    ]),
]

ALL_PERMISSIONS = {key for _, perms in PERMISSION_GROUPS for key, _ in perms}

# Convenience buttons on the user permission page; checkboxes remain the truth
PRESETS = {
    'Viewer': [],
    'Services Editor': ['services.view', 'services.edit'],
    'Display Editor': ['displays.view', 'displays.edit'],
    'Grievance Intake Staff': ['grievances.create'],
    'Grievance Reviewer': ['grievances.view', 'grievances.review',
                           'grievances.attach', 'grievances.close'],
    'PDF Forms Manager': ['pdf_forms.view', 'pdf_forms.edit'],
}

# What the seed script grants existing editor accounts: their legacy editing
# areas, deliberately excluding grievances, PDF templates, and settings
LEGACY_EDITOR_PERMISSIONS = [
    'services.view', 'services.edit',
    'displays.view', 'displays.edit',
    'submissions.view', 'submissions.manage',
]


def is_admin() -> bool:
    """True for the one-password admin session or an admin-role user."""
    if session.get('is_admin') or session.get('admin'):
        return True
    user = getattr(g, 'user', None)
    return bool(user and (getattr(user, 'role', '') or '').lower() == 'admin')


def _granted_permissions(user) -> set[str]:
    """Load (and per-request cache) the user's granted permission keys."""
    cache = getattr(g, '_permission_cache', None)
    if cache is None:
        cache = g._permission_cache = {}
    if user.id not in cache:
        db = current_app.dbs()
        rows = db.query(UserPermission.permission).filter(
            UserPermission.user_id == user.id).all()
        cache[user.id] = {row[0] for row in rows}
    return cache[user.id]


def has_permission(key: str) -> bool:
    """Check the current request's actor for a permission. Admins always pass."""
    if is_admin():
        return True
    user = getattr(g, 'user', None)
    if not user:
        return False
    return key in _granted_permissions(user)


def permission_required(key: str):
    """Route decorator: 403 unless the actor is admin or holds the permission."""
    def deco(fn):
        @wraps(fn)
        def _wrap(*args, **kwargs):
            if not has_permission(key):
                return abort(403)
            return fn(*args, **kwargs)
        return _wrap
    return deco


def permission_required_rw(view_key: str, edit_key: str):
    """Decorator for combined GET/POST routes: reads need view, writes need edit."""
    def deco(fn):
        @wraps(fn)
        def _wrap(*args, **kwargs):
            from flask import request
            key = edit_key if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') else view_key
            if not has_permission(key):
                return abort(403)
            return fn(*args, **kwargs)
        return _wrap
    return deco


def get_permissions(db, user_id: int) -> set[str]:
    """Return the permission keys granted to a user."""
    rows = db.query(UserPermission.permission).filter(
        UserPermission.user_id == user_id).all()
    return {row[0] for row in rows}


def set_permissions(db, user_id: int, keys) -> set[str]:
    """Replace a user's grants with ``keys`` (unknown keys dropped). No commit."""
    wanted = {k for k in keys if k in ALL_PERMISSIONS}
    existing = {
        row.permission: row
        for row in db.query(UserPermission).filter(UserPermission.user_id == user_id).all()
    }
    for key, row in existing.items():
        if key not in wanted:
            db.delete(row)
    for key in wanted - set(existing):
        db.add(UserPermission(user_id=user_id, permission=key))
    return wanted


def grant_permissions(db, user_id: int, keys) -> list[str]:
    """Add grants without removing existing ones; returns newly added keys."""
    current = get_permissions(db, user_id)
    added = []
    for key in keys:
        if key in ALL_PERMISSIONS and key not in current:
            db.add(UserPermission(user_id=user_id, permission=key))
            added.append(key)
    return added
