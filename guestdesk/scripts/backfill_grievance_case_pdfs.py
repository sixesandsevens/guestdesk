#!/usr/bin/env python3
"""Attach the system-generated grievance PDF to cases that are missing one.

Dry-run by default; pass --apply to write. For each case without a
system_generated_pdf attachment, prefer copying the archived render from the
PDF output directory (the exact document originally emailed); otherwise
re-render through the current grievance template. Cases are skipped when no
archive exists and no PDF template is configured. Idempotent.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from guestdesk import pdf_config
from guestdesk.models import Base, Submission, GrievanceCase
from guestdesk.grievances import (
    GENERATED_PDF_TYPE,
    attach_generated_pdf,
    case_generated_pdf,
    render_case_pdf,
)


def default_db_path() -> Path:
    """Match the application's default SQLite location."""
    data_dir = (
        os.environ.get("GUESTDESK_DATA_DIR")
        or os.environ.get("GUESTD_DATA_DIR")
        or "/var/lib/guestdesk"
    )
    return Path(data_dir) / "guestdesk.db"


def archived_pdf_path(submission_id: int) -> Path:
    """Location where the submit flow archives rendered grievance PDFs."""
    return Path(pdf_config.output_root()) / "grievance" / str(submission_id) / f"grievance-{submission_id}.pdf"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Attach generated grievance PDFs to cases missing one. "
                    "Dry-run by default; pass --apply to write."
    )
    parser.add_argument("--db", type=Path, default=default_db_path(),
                        help=f"SQLite database path (default: {default_db_path()})")
    parser.add_argument("--apply", action="store_true", help="Write the attachments")
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
    attached = copied = skipped = 0
    try:
        cases = db.query(GrievanceCase).order_by(GrievanceCase.id).all()
        for case in cases:
            if case_generated_pdf(case):
                continue
            submission = db.get(Submission, case.submission_id)
            if not submission:
                print(f"SKIP {case.public_reference}: submission #{case.submission_id} missing")
                skipped += 1
                continue
            archive = archived_pdf_path(case.submission_id)
            if archive.is_file():
                if args.apply:
                    attach_generated_pdf(db, case, archive.read_bytes(), actor_label='backfill')
                    copied += 1
                    print(f"COPIED archived PDF -> {case.public_reference}")
                else:
                    print(f"Would copy archived PDF -> {case.public_reference}")
                continue
            if args.apply:
                pdf_bytes = render_case_pdf(db, case, submission)
                if not pdf_bytes:
                    print(f"SKIP {case.public_reference}: no archive and no PDF template configured")
                    skipped += 1
                    continue
                attach_generated_pdf(db, case, pdf_bytes, actor_label='backfill')
                attached += 1
                print(f"RENDERED PDF -> {case.public_reference}")
            else:
                print(f"Would render PDF -> {case.public_reference} (no archive found)")
        if args.apply:
            db.commit()
            print(f"Done: {copied} copied, {attached} rendered, {skipped} skipped.")
        else:
            missing = [c for c in cases if not case_generated_pdf(c)]
            print(f"{len(missing)} case(s) missing a generated PDF. Re-run with --apply to attach them.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
