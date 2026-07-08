"""Grievance tracker: case lifecycle helpers and the admin blueprint.

The public /submit/grievance form (in app.py) remains the guest intake path;
this module owns everything after intake — the GrievanceCase record, staff
data entry for paper/verbal grievances, assignment, status, notes,
attachments, and the case timeline.
"""

# GuestDesk
# Copyright (c) 2025 Chris Tanton
# SPDX-License-Identifier: LicenseRef-GDCL-1.1
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    abort, session, g, current_app, send_file
)
from werkzeug.utils import secure_filename

from .audit import log as audit_log
from .mailer import queue_mail, _recipient_for
from .models import (
    Submission,
    User,
    FormPDFConfig,
    GrievanceCase,
    GrievanceAttachment,
    GrievanceNote,
    GrievanceEvent,
)

DATA_DIR = (
    os.environ.get("GUESTDESK_DATA_DIR")
    or os.environ.get("GUESTD_DATA_DIR")
    or "/var/lib/guestdesk"
)

# Policy timelines (business days)
ACKNOWLEDGEMENT_DUE_DAYS = 5
RESPONSE_DUE_DAYS = 15
ADDITIONAL_REVIEW_DUE_DAYS = 10

SOURCES = {
    'guest_digital': 'Guest digital form',
    'paper': 'Paper / handwritten',
    'verbal': 'Verbal',
    'staff_assisted': 'Staff-assisted digital',
}
# Sources where the original document must be scanned/photographed and attached
SOURCES_REQUIRING_ATTACHMENT = {'paper', 'verbal'}

STATUSES = {
    'received': 'Received',
    'acknowledged': 'Acknowledged',
    'in_review': 'In review',
    'response_provided': 'Response provided',
    'additional_review': 'Additional review',
    'closed': 'Closed',
}
# response_provided stays open: the case still needs closure (and may go to additional review)
OPEN_STATUSES = ('received', 'acknowledged', 'in_review', 'response_provided', 'additional_review')

# Reserved for the PDF GuestDesk renders itself; the only kind that may be emailed
GENERATED_PDF_TYPE = 'system_generated_pdf'
GENERATED_PDF_FILENAME = 'generated-grievance.pdf'

ATTACHMENT_TYPES = {
    'original_handwritten_grievance': 'Original handwritten grievance',
    'verbal_grievance_documentation': 'Verbal grievance documentation',
    'supporting_documentation': 'Supporting documentation',
    'photo': 'Photo',
    GENERATED_PDF_TYPE: 'Generated grievance PDF',
    'other': 'Other',
}
ATTACHMENT_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}

NOTE_TYPES = {
    'internal': 'Internal',
    'investigation': 'Investigation',
    'guest_contact': 'Guest contact',
    'supervisor_review': 'Supervisor review',
    'closure': 'Closure',
}


def ensure_archive_columns(engine) -> None:
    """Add the archive columns to grievance_cases on databases that predate them.

    create_all() only creates missing tables, so column additions need this
    lightweight migration (same pattern as the users/services migrations).
    """
    with engine.begin() as conn:
        cols = [r[1] for r in conn.exec_driver_sql('PRAGMA table_info(grievance_cases)').all()]
        if not cols:
            return  # table doesn't exist yet; create_all will make it complete
        if 'archived_at' not in cols:
            conn.exec_driver_sql('ALTER TABLE grievance_cases ADD COLUMN archived_at DATETIME')
        if 'archived_by_user_id' not in cols:
            conn.exec_driver_sql('ALTER TABLE grievance_cases ADD COLUMN archived_by_user_id INTEGER')


def build_grievance_case_id(submission_id: int, created_at: datetime | None) -> str:
    """Generate a stable grievance case identifier."""
    created = created_at or datetime.utcnow()
    created_utc = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
    return f"GRV-{submission_id}-{created_utc.strftime('%Y')}-{int(created_utc.timestamp())}"


def add_business_days(start: datetime, days: int) -> datetime:
    """Return ``start`` advanced by ``days`` business days (weekends excluded)."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Mon-Fri
            remaining -= 1
    return current


def log_case_event(db, case: GrievanceCase, event_type: str, *,
                   actor_label: str = 'system', actor_user_id: int | None = None,
                   old_value: str | None = None, new_value: str | None = None,
                   meta: dict | None = None) -> GrievanceEvent:
    """Append a timeline event to a case (does not commit)."""
    event = GrievanceEvent(
        case_id=case.id,
        actor_user_id=actor_user_id,
        actor_label=actor_label or 'system',
        event_type=event_type,
        old_value=old_value,
        new_value=new_value,
        meta_json=json.dumps(meta) if meta else None,
    )
    db.add(event)
    return event


def _form_checkbox(form, *names) -> bool:
    """Return True when any of the named form fields is truthy."""
    return any((form.get(name) or '').strip() for name in names)


def create_case_for_submission(db, submission: Submission, *,
                               source: str = 'guest_digital',
                               form=None,
                               original_received_at: datetime | None = None,
                               entered_by_user_id: int | None = None,
                               intake_notes: str | None = None,
                               actor_label: str = 'guest') -> GrievanceCase:
    """Create the GrievanceCase for a grievance submission (does not commit).

    ``form`` is the submitted form mapping; the grievance-specific fields
    (staff involved, category checkboxes, incident date/time) only exist there,
    so they must be captured here or they survive only inside the rendered PDF.
    """
    form = form or {}
    received = original_received_at or submission.created_at or datetime.utcnow()
    other_text = (form.get('involves_other') or form.get('involves_other_txt') or '').strip() or None
    case = GrievanceCase(
        submission_id=submission.id,
        public_reference=build_grievance_case_id(submission.id, submission.created_at),
        source=source if source in SOURCES else 'guest_digital',
        original_received_at=received,
        entered_by_user_id=entered_by_user_id,
        status='received',
        staff_involved=(form.get('staff_involved') or form.get('name_of_staff_involved') or '').strip() or None,
        involves_grace_staff=_form_checkbox(form, 'involves_grace_staff', 'involves_staff'),
        involves_policies=_form_checkbox(form, 'involves_policies'),
        involves_volunteer=_form_checkbox(form, 'involves_volunteer'),
        involves_other=bool(_form_checkbox(form, 'involves_other_chk') or other_text),
        involves_other_text=other_text,
        incident_date=(form.get('incident_date') or '').strip() or None,
        incident_time=(form.get('incident_time') or '').strip() or None,
        intake_notes=(intake_notes or '').strip() or None,
        acknowledgement_due_at=add_business_days(received, ACKNOWLEDGEMENT_DUE_DAYS),
        response_due_at=add_business_days(received, RESPONSE_DUE_DAYS),
    )
    db.add(case)
    db.flush()
    log_case_event(db, case, 'case_created', actor_label=actor_label,
                   actor_user_id=entered_by_user_id,
                   new_value=case.status, meta={'source': case.source})
    return case


def case_upload_root(case: GrievanceCase) -> Path:
    """Directory where a case's attachments are stored (shared with Submission uploads)."""
    return Path(DATA_DIR) / 'uploads' / 'grievance' / str(case.submission_id)


def save_case_attachment(db, case: GrievanceCase, file_storage, *,
                         attachment_type: str = 'supporting_documentation',
                         uploaded_by_user_id: int | None = None,
                         actor_label: str = 'staff') -> GrievanceAttachment:
    """Validate and store a human-uploaded file for a case (does not commit).

    Raises ``ValueError`` with a user-facing message when the file is unusable.
    The system_generated_pdf type is reserved for attach_generated_pdf().
    """
    if attachment_type == GENERATED_PDF_TYPE:
        raise ValueError('That attachment type is reserved for the system-generated PDF.')
    filename = secure_filename(file_storage.filename or '')
    ext = Path(filename).suffix.lower()
    if ext not in ATTACHMENT_EXTENSIONS:
        raise ValueError('Please upload a PDF, JPG, or PNG file.')
    data = file_storage.read()
    if not data:
        raise ValueError('The uploaded file appears to be empty.')
    if ext == '.pdf' and not data.startswith(b'%PDF'):
        raise ValueError('The uploaded PDF does not appear to be valid.')
    if ext in ('.jpg', '.jpeg') and not data.startswith(b'\xff\xd8'):
        raise ValueError('The uploaded JPG does not appear to be valid.')
    if ext == '.png' and not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('The uploaded PNG does not appear to be valid.')
    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    stored_name = f"{timestamp}_{filename or 'attachment' + ext}"
    root = case_upload_root(case)
    root.mkdir(parents=True, exist_ok=True)
    path = root / stored_name
    with open(path, 'wb') as fh:
        fh.write(data)
    attachment = GrievanceAttachment(
        case_id=case.id,
        attachment_type=attachment_type if attachment_type in ATTACHMENT_TYPES else 'other',
        original_filename=filename or stored_name,
        stored_filename=stored_name,
        storage_path=str(path),
        uploaded_by_user_id=uploaded_by_user_id,
    )
    db.add(attachment)
    log_case_event(db, case, 'attachment_uploaded', actor_label=actor_label,
                   actor_user_id=uploaded_by_user_id,
                   new_value=attachment.original_filename,
                   meta={'attachment_type': attachment.attachment_type})
    return attachment


# ---------- Generated case PDF ----------

def _split_contact_info(contact_info: str | None) -> tuple[str, str]:
    """Split Submission.contact_info ('phone, email') back into (phone, email)."""
    parts = [p.strip() for p in (contact_info or '').split(',') if p.strip()]
    email = next((p for p in parts if '@' in p), '')
    phone = ', '.join(p for p in parts if p != email)
    return phone, email


def _format_received(received: datetime | None) -> str:
    """Format the received timestamp the way it appears on the intake header."""
    if not received:
        return ''
    return f"{received.strftime('%m/%d/%Y')} {received.strftime('%I:%M %p').lstrip('0')}"


def _entered_by_label(case: GrievanceCase) -> str | None:
    """Best available name for who entered the grievance into GuestDesk."""
    if case.entered_by:
        return case.entered_by.username
    created = next((e for e in case.events if e.event_type == 'case_created'), None)
    if created and created.actor_label not in ('guest', 'system', 'backfill'):
        return created.actor_label
    return None


def build_case_pdf_payload(case: GrievanceCase, submission: Submission) -> dict:
    """Build the grievance PDF field map from the stored case record.

    Mirrors the public form's payload so staff-entered and backfilled cases
    render through the same template and layout as guest submissions.
    """
    phone, email = _split_contact_info(submission.contact_info)
    return {
        'id': case.public_reference,
        'case_id': case.public_reference,
        'submission_id': submission.id,
        'todays_date': datetime.utcnow().strftime('%Y-%m-%d'),
        'submitted_date': case.original_received_at.strftime('%Y-%m-%d') if case.original_received_at else '',
        'submitted_time': case.original_received_at.strftime('%I:%M %p').lstrip('0') if case.original_received_at else '',
        'staff_involved': case.staff_involved or '',
        'name': submission.contact_name or '',
        'contact_name': submission.contact_name or '',
        'phone': phone,
        'email': email,
        'involves_staff': case.involves_grace_staff,
        'involves_grace_staff': case.involves_grace_staff,
        'involves_policies': case.involves_policies,
        'involves_volunteer': case.involves_volunteer,
        'involves_other': case.involves_other,
        'involves_other_txt': case.involves_other_text or '',
        'other': case.involves_other_text or '',
        'incident_date': case.incident_date or '',
        'incident_time': case.incident_time or '',
        'description': submission.body or '',
    }


def intake_header_lines(case: GrievanceCase, *, include_reference: bool = True) -> list[str]:
    """Reference/source lines stamped in the PDF's upper-right header area.

    ``include_reference`` is False when the bound layout already prints the
    case reference, so the stamp doesn't double-print on top of it.
    """
    lines = []
    if include_reference:
        lines.append(f"Reference: {case.public_reference}")
    lines += [
        f"Source: {SOURCES.get(case.source, case.source)}",
        f"Received: {_format_received(case.original_received_at)}",
    ]
    entered_by = _entered_by_label(case)
    if entered_by:
        lines.append(f"Entered by: {entered_by}")
    return lines


def _stamp_intake_header(pdf_bytes: bytes, lines: list[str], *,
                         start_y: float | None = None) -> bytes:
    """Overlay the intake header block on the top-right of page 1.

    ``start_y`` is the baseline (bottom-left points) for the first line;
    defaults to just inside the top page edge.
    """
    from reportlab.pdfgen import canvas
    from PyPDF2 import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        if index == 0:
            box = page.mediabox
            width = float(box.right - box.left)
            height = float(box.top - box.bottom)
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(width, height))
            c.setFont('Helvetica', 7)
            y = start_y if start_y is not None else height - 16
            for line in lines:
                c.drawRightString(width - 20, y, line)
                y -= 9
            c.showPage()
            c.save()
            overlay = PdfReader(io.BytesIO(buf.getvalue()))
            page.merge_page(overlay.pages[0])
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def grievance_pdf_config(db) -> FormPDFConfig | None:
    """Return the grievance PDF config when a usable template is bound."""
    cfg = db.query(FormPDFConfig).filter(FormPDFConfig.form_key == 'grievance').first()
    if cfg and cfg.template_path and cfg.layout_json:
        return cfg
    return None


def render_case_pdf(db, case: GrievanceCase, submission: Submission) -> bytes | None:
    """Render the standard grievance PDF for a case, or None when unconfigured.

    Uses the same template/layout as the public form; the only difference is
    the intake header stamped in the top-right corner.
    """
    cfg = grievance_pdf_config(db)
    if not cfg:
        return None
    from .pdf_render import render_pdf
    data = build_case_pdf_payload(case, submission)
    pdf_bytes = render_pdf(cfg.template_path, cfg.layout_json, data,
                           pad=float(cfg.baseline_pad or 3), debug=False)
    # When the layout already prints the reference in the header, skip the
    # stamp's Reference line and anchor the stamp just below the printed one.
    try:
        layout = json.loads(cfg.layout_json) if isinstance(cfg.layout_json, str) else (cfg.layout_json or {})
    except Exception:
        layout = {}
    ref_box = layout.get('id') or layout.get('case_id')
    include_reference = ref_box is None
    start_y = None
    if isinstance(ref_box, (list, tuple)) and len(ref_box) >= 2:
        try:
            start_y = float(ref_box[1]) - 8
        except (TypeError, ValueError):
            start_y = None
    lines = intake_header_lines(case, include_reference=include_reference)
    return _stamp_intake_header(pdf_bytes, lines, start_y=start_y)


def case_generated_pdf(case: GrievanceCase) -> GrievanceAttachment | None:
    """Return the case's system-generated PDF attachment, if present."""
    return next((a for a in case.attachments if a.attachment_type == GENERATED_PDF_TYPE), None)


def attach_generated_pdf(db, case: GrievanceCase, pdf_bytes: bytes, *,
                         actor_label: str = 'system',
                         actor_user_id: int | None = None) -> GrievanceAttachment:
    """Store the generated PDF in the trusted upload root as a system attachment.

    Idempotent: an existing system_generated_pdf attachment is returned as-is.
    Does not commit.
    """
    existing = case_generated_pdf(case)
    if existing:
        return existing
    root = case_upload_root(case)
    root.mkdir(parents=True, exist_ok=True)
    path = root / GENERATED_PDF_FILENAME
    with open(path, 'wb') as fh:
        fh.write(pdf_bytes)
    attachment = GrievanceAttachment(
        case_id=case.id,
        attachment_type=GENERATED_PDF_TYPE,
        original_filename=f"{case.public_reference}.pdf",
        stored_filename=GENERATED_PDF_FILENAME,
        storage_path=str(path),
        uploaded_by_user_id=None,
    )
    db.add(attachment)
    log_case_event(db, case, 'pdf_generated', actor_label=actor_label,
                   actor_user_id=actor_user_id, new_value=attachment.original_filename)
    return attachment


def send_staff_intake_notification(case: GrievanceCase, submission: Submission,
                                   generated_pdf: bytes | None) -> bool:
    """Notify grievance reviewers that staff entered a grievance.

    Deliberately takes only the generated PDF bytes — never the case's
    attachment list — so human-uploaded files can never be emailed.
    """
    recipients = _recipient_for('grievance')
    if not recipients:
        return False
    entered_by = _entered_by_label(case) or 'staff'
    lines = [
        'A grievance was entered into GuestDesk by staff.',
        '',
        f'Reference: {case.public_reference}',
        f'Source: {SOURCES.get(case.source, case.source)}',
        f'Received: {_format_received(case.original_received_at)}',
        f'Entered by: {entered_by}',
        '',
    ]
    if generated_pdf:
        lines.append('The generated grievance PDF is attached.')
    else:
        lines.append('No grievance PDF template is configured; view the case in GuestDesk.')
    lines.append('')
    lines.append('Uploaded documentation and original handwritten grievance files are '
                 'stored in the GuestDesk case file and are not attached to this email.')
    attachments = None
    if generated_pdf:
        attachments = [('application/pdf', f'{case.public_reference}.pdf', generated_pdf)]
    queue_mail(
        subject=f'[GuestDesk] Grievance (staff-entered) {case.public_reference}',
        body='\n'.join(lines),
        to=recipients,
        attachments=attachments,
    )
    return True


# ---------- Admin blueprint ----------

bp = Blueprint('grievances', __name__, url_prefix='/admin/grievances')


def _dbs():
    """Use the app's scoped session factory."""
    return current_app.dbs()


def _actor():
    """Return (label, user_id) identifying who is acting, mirroring audit_actor()."""
    user = getattr(g, 'user', None)
    if user and getattr(user, 'username', None):
        return str(user.username), user.id
    if session.get('is_admin') or session.get('admin'):
        return 'admin-session', None
    return 'anonymous', None


def roles_required(*required_roles):
    """Role gate matching the app-level decorator (one-password admin bypasses)."""
    def deco(fn):
        @wraps(fn)
        def _wrap(*a, **kw):
            if session.get('is_admin') or session.get('admin'):
                return fn(*a, **kw)
            u = getattr(g, 'user', None)
            if u and ((getattr(u, 'role', '') or '').lower() in [r.lower() for r in required_roles]):
                return fn(*a, **kw)
            return abort(403)
        return _wrap
    return deco


def _get_case(db, case_id: int) -> GrievanceCase:
    case = db.get(GrievanceCase, case_id)
    if not case:
        abort(404)
    return case


def _case_flags(case: GrievanceCase, now: datetime) -> dict:
    """Compute display flags for dashboard rows."""
    open_case = case.status in OPEN_STATUSES and not case.archived_at
    needs_ack = open_case and not case.acknowledged_at
    ack_overdue = needs_ack and case.acknowledgement_due_at and case.acknowledgement_due_at < now
    response_pending = open_case and not case.response_provided_at
    response_overdue = response_pending and case.response_due_at and case.response_due_at < now
    due_soon = (
        response_pending and not response_overdue
        and case.response_due_at and case.response_due_at <= now + timedelta(days=3)
    )
    return {
        'open': open_case,
        'needs_ack': needs_ack,
        'ack_overdue': ack_overdue,
        'overdue': bool(response_overdue or ack_overdue),
        'due_soon': bool(due_soon),
        'unassigned': open_case and not case.assigned_reviewer_id,
    }


@bp.route('/')
@roles_required('admin', 'editor')
def dashboard():
    """Grievance work queue: open cases, deadlines, and filters."""
    db = _dbs()
    now = datetime.utcnow()
    view = (request.args.get('view') or 'open').strip()
    cases = (
        db.query(GrievanceCase)
        .order_by(GrievanceCase.original_received_at.desc())
        .limit(1000)
        .all()
    )
    archived = [{'case': c, 'flags': _case_flags(c, now)} for c in cases if c.archived_at]
    rows = [{'case': c, 'flags': _case_flags(c, now)} for c in cases if not c.archived_at]
    counts = {
        'open': sum(1 for r in rows if r['flags']['open']),
        'unassigned': sum(1 for r in rows if r['flags']['unassigned']),
        'needs_ack': sum(1 for r in rows if r['flags']['needs_ack']),
        'due_soon': sum(1 for r in rows if r['flags']['due_soon']),
        'overdue': sum(1 for r in rows if r['flags']['overdue']),
        'additional_review': sum(1 for r in rows if r['case'].status == 'additional_review'),
        'closed': sum(1 for r in rows if r['case'].status == 'closed'),
        'all': len(rows),
        'archived': len(archived),
    }
    if view == 'archived':
        visible = archived
    elif view == 'all':
        visible = rows
    elif view == 'closed':
        visible = [r for r in rows if r['case'].status == 'closed']
    elif view == 'additional_review':
        visible = [r for r in rows if r['case'].status == 'additional_review']
    elif view in ('unassigned', 'needs_ack', 'due_soon', 'overdue'):
        visible = [r for r in rows if r['flags'][view]]
    else:
        view = 'open'
        visible = [r for r in rows if r['flags']['open']]
    return render_template(
        'admin/grievances.html',
        rows=visible, counts=counts, view=view, now=now,
        statuses=STATUSES, sources=SOURCES,
    )


@bp.route('/new', methods=['GET', 'POST'])
@roles_required('admin', 'editor')
def new_case():
    """Staff data entry for paper, verbal, and staff-assisted grievances."""
    db = _dbs()
    if request.method == 'POST':
        form = request.form
        source = (form.get('source') or '').strip()
        received_date = (form.get('received_date') or '').strip()
        name = (form.get('name') or '').strip()
        description = (form.get('description') or '').strip()
        errors = []
        if source not in SOURCES or source == 'guest_digital':
            errors.append('Please choose how the grievance was received.')
        if not received_date:
            errors.append('Original received date is required.')
        if not name:
            errors.append('Complainant name is required.')
        if not description:
            errors.append('The grievance text is required.')
        received_at = None
        if received_date:
            received_time = (form.get('received_time') or '').strip()
            try:
                received_at = datetime.strptime(
                    f"{received_date} {received_time or '00:00'}", '%Y-%m-%d %H:%M'
                )
            except ValueError:
                errors.append('Original received date/time is not valid.')
            else:
                if received_at > datetime.utcnow() + timedelta(days=1):
                    errors.append('Original received date cannot be in the future.')
        attachment_file = request.files.get('attachment')
        has_attachment = bool(attachment_file and attachment_file.filename)
        if source in SOURCES_REQUIRING_ATTACHMENT and not has_attachment:
            label = 'scanned original grievance' if source == 'paper' else 'staff-written documentation'
            errors.append(f'A {label} attachment is required for this source.')
        if errors:
            for msg in errors:
                flash(msg, 'danger')
            return render_template('admin/grievance_new.html', form=form,
                                   sources=SOURCES, attachment_types=ATTACHMENT_TYPES)

        actor_label, actor_user_id = _actor()
        contact_bits = [(form.get('phone') or '').strip(), (form.get('email') or '').strip()]
        submission = Submission(
            kind='grievance',
            body=description,
            contact_name=name,
            contact_info=', '.join(b for b in contact_bits if b) or None,
        )
        db.add(submission)
        db.flush()
        case = create_case_for_submission(
            db, submission,
            source=source,
            form=form,
            original_received_at=received_at,
            entered_by_user_id=actor_user_id,
            intake_notes=form.get('intake_notes'),
            actor_label=actor_label,
        )
        if has_attachment:
            default_type = ('original_handwritten_grievance' if source == 'paper'
                            else 'verbal_grievance_documentation' if source == 'verbal'
                            else 'supporting_documentation')
            try:
                save_case_attachment(
                    db, case, attachment_file,
                    attachment_type=(form.get('attachment_type') or default_type),
                    uploaded_by_user_id=actor_user_id,
                    actor_label=actor_label,
                )
            except ValueError as exc:
                db.rollback()
                flash(str(exc), 'danger')
                return render_template('admin/grievance_new.html', form=form,
                                       sources=SOURCES, attachment_types=ATTACHMENT_TYPES)
        db.commit()
        audit_log('grievance.case.staff_entered', actor=actor_label, obj=case.public_reference,
                  extra={'submission_id': submission.id, 'source': source})
        # Generate and attach the standard grievance PDF (same layout as the
        # public form); failures must not lose the already-committed case.
        pdf_bytes = None
        try:
            pdf_bytes = render_case_pdf(db, case, submission)
            if pdf_bytes:
                attach_generated_pdf(db, case, pdf_bytes,
                                     actor_label=actor_label, actor_user_id=actor_user_id)
                db.commit()
        except Exception:
            db.rollback()
            pdf_bytes = None
            current_app.logger.exception('Failed to generate PDF for case %s', case.public_reference)
        try:
            cfg = grievance_pdf_config(db)
            email_pdf = pdf_bytes if (pdf_bytes and cfg and cfg.attach_to_email) else None
            if send_staff_intake_notification(case, submission, email_pdf):
                log_case_event(db, case, 'intake_notification_queued', actor_label='system',
                               meta={'pdf_attached': bool(email_pdf)})
                db.commit()
        except Exception:
            db.rollback()
            current_app.logger.exception('Failed to queue intake notification for case %s', case.public_reference)
        flash(f'Grievance case {case.public_reference} created.', 'success')
        return redirect(url_for('grievances.detail', case_id=case.id))
    return render_template('admin/grievance_new.html', form={},
                           sources=SOURCES, attachment_types=ATTACHMENT_TYPES)


@bp.route('/<int:case_id>')
@roles_required('admin', 'editor')
def detail(case_id: int):
    """Case working page: intake data, workflow, notes, attachments, timeline."""
    db = _dbs()
    case = _get_case(db, case_id)
    reviewers = (
        db.query(User)
        .filter(User.approved.is_(True))
        .filter(User.role.in_(['admin', 'editor']))
        .order_by(User.username)
        .all()
    )
    events = sorted(case.events, key=lambda e: (e.created_at or datetime.min), reverse=True)
    notes = sorted(case.notes, key=lambda n: (n.created_at or datetime.min), reverse=True)
    return render_template(
        'admin/grievance_detail.html',
        case=case, sub=case.submission, reviewers=reviewers,
        events=events, notes=notes, now=datetime.utcnow(),
        statuses=STATUSES, sources=SOURCES,
        note_types=NOTE_TYPES, attachment_types=ATTACHMENT_TYPES,
        flags=_case_flags(case, datetime.utcnow()),
    )


@bp.route('/<int:case_id>/status', methods=['POST'])
@roles_required('admin', 'editor')
def update_status(case_id: int):
    """Change case status, stamping the matching lifecycle timestamps."""
    db = _dbs()
    case = _get_case(db, case_id)
    new_status = (request.form.get('status') or '').strip()
    if new_status not in STATUSES:
        flash('Unknown status.', 'danger')
        return redirect(url_for('grievances.detail', case_id=case.id))
    actor_label, actor_user_id = _actor()
    old_status = case.status
    if new_status == old_status:
        flash('Status unchanged.', 'info')
        return redirect(url_for('grievances.detail', case_id=case.id))
    now = datetime.utcnow()
    case.status = new_status
    if new_status == 'acknowledged' and not case.acknowledged_at:
        case.acknowledged_at = now
    if new_status == 'response_provided' and not case.response_provided_at:
        case.response_provided_at = now
        case.response_method = (request.form.get('response_method') or '').strip() or case.response_method
    if new_status == 'additional_review':
        case.additional_review_requested_at = case.additional_review_requested_at or now
        base = case.response_provided_at or now
        case.additional_review_due_at = case.additional_review_due_at or add_business_days(base, ADDITIONAL_REVIEW_DUE_DAYS)
        case.additional_review_status = 'requested'
    if new_status == 'closed':
        case.closed_at = case.closed_at or now
        case.closed_by_user_id = actor_user_id
    elif old_status == 'closed':
        # Reopening
        case.closed_at = None
        case.closed_by_user_id = None
    log_case_event(db, case, 'status_changed', actor_label=actor_label,
                   actor_user_id=actor_user_id, old_value=old_status, new_value=new_status)
    db.commit()
    audit_log('grievance.case.status_changed', actor=actor_label, obj=case.public_reference,
              extra={'from': old_status, 'to': new_status})
    flash(f'Status updated to {STATUSES[new_status]}.', 'success')
    return redirect(url_for('grievances.detail', case_id=case.id))


@bp.route('/<int:case_id>/assign', methods=['POST'])
@roles_required('admin', 'editor')
def assign(case_id: int):
    """Assign or unassign the reviewing staff member."""
    db = _dbs()
    case = _get_case(db, case_id)
    raw = (request.form.get('assigned_reviewer_id') or '').strip()
    reviewer = None
    if raw:
        try:
            reviewer = db.get(User, int(raw))
        except ValueError:
            reviewer = None
        if not reviewer:
            flash('Unknown reviewer.', 'danger')
            return redirect(url_for('grievances.detail', case_id=case.id))
        if not reviewer.approved or (reviewer.role or '').lower() not in ('admin', 'editor'):
            flash('Reviewer must be an approved admin or editor.', 'danger')
            return redirect(url_for('grievances.detail', case_id=case.id))
    actor_label, actor_user_id = _actor()
    old = case.assigned_reviewer.username if case.assigned_reviewer else None
    case.assigned_reviewer_id = reviewer.id if reviewer else None
    log_case_event(db, case, 'assigned', actor_label=actor_label, actor_user_id=actor_user_id,
                   old_value=old, new_value=reviewer.username if reviewer else None)
    db.commit()
    flash(f"Assigned to {reviewer.username}." if reviewer else 'Reviewer unassigned.', 'success')
    return redirect(url_for('grievances.detail', case_id=case.id))


@bp.route('/<int:case_id>/notes', methods=['POST'])
@roles_required('admin', 'editor')
def add_note(case_id: int):
    """Attach a staff note to the case."""
    db = _dbs()
    case = _get_case(db, case_id)
    body = (request.form.get('body') or '').strip()
    if not body:
        flash('Note text is required.', 'danger')
        return redirect(url_for('grievances.detail', case_id=case.id))
    note_type = (request.form.get('note_type') or 'internal').strip()
    actor_label, actor_user_id = _actor()
    note = GrievanceNote(
        case_id=case.id,
        author_user_id=actor_user_id,
        author_label=actor_label,
        note_type=note_type if note_type in NOTE_TYPES else 'internal',
        body=body,
    )
    db.add(note)
    log_case_event(db, case, 'note_added', actor_label=actor_label,
                   actor_user_id=actor_user_id, meta={'note_type': note.note_type})
    db.commit()
    flash('Note added.', 'success')
    return redirect(url_for('grievances.detail', case_id=case.id))


@bp.route('/<int:case_id>/review', methods=['POST'])
@roles_required('admin', 'editor')
def save_review(case_id: int):
    """Save findings, resolution, guest-facing response, and closure notes."""
    db = _dbs()
    case = _get_case(db, case_id)
    actor_label, actor_user_id = _actor()
    changed = []
    for field in ('findings', 'resolution', 'guest_facing_response', 'closure_notes'):
        if field in request.form:
            new_val = (request.form.get(field) or '').strip() or None
            if new_val != getattr(case, field):
                setattr(case, field, new_val)
                changed.append(field)
    if not changed:
        flash('No changes to save.', 'info')
        return redirect(url_for('grievances.detail', case_id=case.id))
    log_case_event(db, case, 'review_updated', actor_label=actor_label,
                   actor_user_id=actor_user_id, meta={'fields': changed})
    db.commit()
    flash('Review details saved.', 'success')
    return redirect(url_for('grievances.detail', case_id=case.id))


@bp.route('/<int:case_id>/archive', methods=['POST'])
@roles_required('admin', 'editor')
def archive(case_id: int):
    """Archive a case: hidden from the tracker's working views, never deleted."""
    db = _dbs()
    case = _get_case(db, case_id)
    if case.archived_at:
        flash('Case is already archived.', 'info')
        return redirect(url_for('grievances.detail', case_id=case.id))
    actor_label, actor_user_id = _actor()
    case.archived_at = datetime.utcnow()
    case.archived_by_user_id = actor_user_id
    log_case_event(db, case, 'archived', actor_label=actor_label, actor_user_id=actor_user_id)
    db.commit()
    audit_log('grievance.case.archived', actor=actor_label, obj=case.public_reference)
    flash(f'Case {case.public_reference} archived.', 'success')
    return redirect(url_for('grievances.dashboard'))


@bp.route('/<int:case_id>/restore', methods=['POST'])
@roles_required('admin', 'editor')
def restore(case_id: int):
    """Bring an archived case back into the tracker."""
    db = _dbs()
    case = _get_case(db, case_id)
    if not case.archived_at:
        flash('Case is not archived.', 'info')
        return redirect(url_for('grievances.detail', case_id=case.id))
    actor_label, actor_user_id = _actor()
    case.archived_at = None
    case.archived_by_user_id = None
    log_case_event(db, case, 'restored', actor_label=actor_label, actor_user_id=actor_user_id)
    db.commit()
    audit_log('grievance.case.restored', actor=actor_label, obj=case.public_reference)
    flash(f'Case {case.public_reference} restored.', 'success')
    return redirect(url_for('grievances.detail', case_id=case.id))


@bp.route('/<int:case_id>/attachments', methods=['POST'])
@roles_required('admin', 'editor')
def upload_attachment(case_id: int):
    """Add a supporting document to an existing case."""
    db = _dbs()
    case = _get_case(db, case_id)
    file_storage = request.files.get('attachment')
    if not file_storage or not file_storage.filename:
        flash('Choose a file to upload.', 'danger')
        return redirect(url_for('grievances.detail', case_id=case.id))
    actor_label, actor_user_id = _actor()
    try:
        save_case_attachment(
            db, case, file_storage,
            attachment_type=(request.form.get('attachment_type') or 'supporting_documentation'),
            uploaded_by_user_id=actor_user_id,
            actor_label=actor_label,
        )
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('grievances.detail', case_id=case.id))
    db.commit()
    flash('Attachment uploaded.', 'success')
    return redirect(url_for('grievances.detail', case_id=case.id))


@bp.route('/<int:case_id>/attachments/<int:attachment_id>')
@roles_required('admin', 'editor')
def download_attachment(case_id: int, attachment_id: int):
    """Serve a stored case attachment."""
    db = _dbs()
    case = _get_case(db, case_id)
    attachment = db.get(GrievanceAttachment, attachment_id)
    if not attachment or attachment.case_id != case.id:
        abort(404)
    root = case_upload_root(case).resolve()
    target = Path(attachment.storage_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        abort(404)
    if not target.is_file():
        abort(404)
    return send_file(target, download_name=attachment.original_filename)
