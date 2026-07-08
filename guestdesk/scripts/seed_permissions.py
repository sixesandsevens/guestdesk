#!/usr/bin/env python3
"""Seed checkbox permissions for existing accounts after the v0.3 upgrade.

Dry-run by default; pass --apply to write. Grants:

- admin users: nothing (the admin role bypasses all permission checks)
- editor users: their legacy editing areas (services, displays, submissions)
  — deliberately NOT grievances, PDF templates, or settings
- viewer users: nothing

Idempotent: existing grants are never duplicated or removed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from guestdesk.models import Base, User
from guestdesk.permissions import LEGACY_EDITOR_PERMISSIONS, grant_permissions, get_permissions


def default_db_path() -> Path:
    """Match the application's default SQLite location."""
    data_dir = (
        os.environ.get("GUESTDESK_DATA_DIR")
        or os.environ.get("GUESTD_DATA_DIR")
        or "/var/lib/guestdesk"
    )
    return Path(data_dir) / "guestdesk.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed permissions for existing users. Dry-run by default; pass --apply to write."
    )
    parser.add_argument("--db", type=Path, default=default_db_path(),
                        help=f"SQLite database path (default: {default_db_path()})")
    parser.add_argument("--apply", action="store_true", help="Write the grants")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        print(f"Database not found: {args.db}", file=sys.stderr)
        return 1
    engine = create_engine(f"sqlite:///{args.db}", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    try:
        changed = 0
        for user in db.query(User).order_by(User.username).all():
            role = (user.role or '').lower()
            if role == 'admin':
                print(f"{user.username}: admin — bypasses permission checks, nothing to grant")
                continue
            if role != 'editor':
                print(f"{user.username}: {role or 'viewer'} — no automatic grants")
                continue
            missing = [k for k in LEGACY_EDITOR_PERMISSIONS
                       if k not in get_permissions(db, user.id)]
            if not missing:
                print(f"{user.username}: editor — already seeded")
                continue
            if args.apply:
                grant_permissions(db, user.id, missing)
                changed += 1
                print(f"{user.username}: editor — granted {', '.join(missing)}")
            else:
                print(f"{user.username}: editor — would grant {', '.join(missing)}")
        if args.apply:
            db.commit()
            print(f"Done: seeded {changed} user(s).")
        else:
            print("Dry run only. Re-run with --apply to write grants.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
