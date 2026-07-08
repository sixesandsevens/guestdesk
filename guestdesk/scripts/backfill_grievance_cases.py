#!/usr/bin/env python3
"""Create GrievanceCase records for grievance submissions that predate the tracker.

Dry-run by default; pass --apply to write. Backfilled cases get
source='guest_digital' and due dates computed from the original submission
time. Intake-only fields (staff involved, incident date/time, categories)
cannot be recovered — for old submissions they exist only inside the
generated PDF, so they are left blank and can be re-keyed manually.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from guestdesk.models import Base, Submission, GrievanceCase
from guestdesk.grievances import create_case_for_submission, ensure_case_columns


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
        description="Create tracker cases for grievance submissions without one. "
                    "Dry-run by default; pass --apply to write."
    )
    parser.add_argument("--db", type=Path, default=default_db_path(),
                        help=f"SQLite database path (default: {default_db_path()})")
    parser.add_argument("--apply", action="store_true", help="Write the new cases")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        print(f"Database not found: {args.db}", file=sys.stderr)
        return 1
    engine = create_engine(f"sqlite:///{args.db}", future=True)
    Base.metadata.create_all(engine)
    ensure_case_columns(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    try:
        missing = (
            db.query(Submission)
            .outerjoin(GrievanceCase, GrievanceCase.submission_id == Submission.id)
            .filter(Submission.kind == 'grievance')
            .filter(GrievanceCase.id.is_(None))
            .order_by(Submission.id)
            .all()
        )
        if not missing:
            print("All grievance submissions already have tracker cases.")
            return 0
        for sub in missing:
            if args.apply:
                case = create_case_for_submission(
                    db, sub, source='guest_digital', actor_label='backfill'
                )
                print(f"Created {case.public_reference} for submission #{sub.id} ({sub.created_at})")
            else:
                print(f"Would create case for submission #{sub.id} ({sub.created_at})")
        if args.apply:
            db.commit()
            print(f"Backfilled {len(missing)} grievance case(s).")
        else:
            print(f"{len(missing)} grievance submission(s) need cases. Re-run with --apply to create them.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
