"""File-based audit logging utilities."""

# GuestDesk
# Copyright (c) 2025 Chris Tanton
# SPDX-License-Identifier: LicenseRef-GDCL-1.1
import json
import logging
import os
from datetime import datetime
from pathlib import Path

_log_path = os.getenv("GUESTDESK_AUDIT_LOG", "/var/log/guestdesk/audit.log")
log_dir = Path(_log_path).parent
if log_dir and not log_dir.exists():
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

audit_log = logging.getLogger("guestdesk.audit")
if not audit_log.handlers:
    try:
        handler = logging.FileHandler(_log_path)
    except PermissionError:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    audit_log.addHandler(handler)
    audit_log.setLevel(logging.INFO)


def log(action, actor, obj=None, before=None, after=None, extra=None):
    """Append a structured audit entry to the configured log target."""
    audit_log.info(json.dumps({
        "ts": datetime.utcnow().isoformat() + "Z",
        "action": action,
        "actor": actor,
        "obj": obj,
        "before": before,
        "after": after,
        "extra": extra or {}
    }))
