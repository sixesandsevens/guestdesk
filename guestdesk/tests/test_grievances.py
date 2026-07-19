import io
import json
from pathlib import Path

from guestdesk.models import (
    FormPDFConfig,
    GrievanceCase,
    GrievanceAttachment,
    GrievanceEvent,
    Submission,
)
from guestdesk.grievances import (
    CLOSURE_REPORT_TYPE,
    GENERATED_PDF_TYPE,
    add_business_days,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fakepixels"
PDF_BYTES = b"%PDF-1.4 fake staff documentation"


def _enable_grievance_pdf(app, tmp_path):
    """Bind a real (blank) template so grievance PDF rendering is active."""
    from reportlab.pdfgen import canvas

    template = tmp_path / "grievance-template.pdf"
    c = canvas.Canvas(str(template), pagesize=(612, 792))
    c.showPage()
    c.save()
    layout = json.dumps({
        "id": [360, 750, 220, 14],
        "name": [72, 700, 220, 14],
        "staff_involved": [72, 670, 220, 14],
        "description": [72, 400, 468, 200],
        "involves_grace_staff": [100, 650],
    })
    with app.app_context():
        db = app.dbs()
        db.add(FormPDFConfig(form_key="grievance", template_path=str(template),
                             layout_json=layout, attach_to_email=True))
        db.commit()


def _capture_staff_mail(monkeypatch, app):
    """Collect intake notifications queued by the grievances module."""
    import guestdesk.grievances as grievances_module

    sent = []
    monkeypatch.setattr(grievances_module, "queue_mail", lambda **kw: sent.append(kw))
    app.config["GRIEVANCE_EMAIL_TO"] = ["reviewers@example.org"]
    return sent


def _pdf_text(path):
    from PyPDF2 import PdfReader

    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _make_app(monkeypatch, tmp_path):
    import guestdesk.app as app_module
    import guestdesk.grievances as grievances_module

    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(grievances_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "queue_mail", lambda **kwargs: None)
    app = app_module.create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s["is_admin"] = True
    return client


def test_add_business_days_skips_weekends():
    from datetime import datetime

    friday = datetime(2026, 7, 3)  # Friday
    assert add_business_days(friday, 1).weekday() == 0  # Monday
    assert (add_business_days(friday, 5) - friday).days == 7


def test_public_grievance_creates_case_with_intake_fields(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with app.test_client() as client:
        resp = client.post(
            "/submit/grievance",
            data={
                "description": "Documented complaint.",
                "name": "Guest One",
                "phone": "555-0100",
                "staff_involved": "J. Doe",
                "involves_grace_staff": "on",
                "incident_date": "2026-07-06",
                "incident_time": "14:30",
            },
        )
    assert resp.status_code == 200
    with app.app_context():
        db = app.dbs()
        case = db.query(GrievanceCase).one()
        assert case.source == "guest_digital"
        assert case.status == "received"
        assert case.staff_involved == "J. Doe"
        assert case.involves_grace_staff is True
        assert case.incident_date == "2026-07-06"
        assert case.acknowledgement_due_at is not None
        assert case.response_due_at is not None
        events = db.query(GrievanceEvent).filter_by(case_id=case.id).all()
        assert any(e.event_type == "case_created" for e in events)


def test_staff_paper_entry_requires_attachment(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    resp = client.post(
        "/admin/grievances/new",
        data={
            "source": "paper",
            "received_date": "2026-07-03",
            "name": "Guest Two",
            "description": "Handwritten complaint.",
        },
    )
    assert resp.status_code == 200
    assert b"attachment is required" in resp.data
    with app.app_context():
        assert app.dbs().query(GrievanceCase).count() == 0


def test_staff_paper_entry_with_scan_creates_case(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    resp = client.post(
        "/admin/grievances/new",
        data={
            "source": "paper",
            "received_date": "2026-07-03",
            "received_time": "09:15",
            "name": "Guest Two",
            "description": "Handwritten complaint.",
            "attachment": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"fakepixels"), "scan.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        db = app.dbs()
        case = db.query(GrievanceCase).one()
        assert case.source == "paper"
        assert case.original_received_at.strftime("%Y-%m-%d %H:%M") == "2026-07-03 09:15"
        attachment = db.query(GrievanceAttachment).filter_by(case_id=case.id).one()
        assert attachment.attachment_type == "original_handwritten_grievance"
        submission = db.get(Submission, case.submission_id)
        assert submission.kind == "grievance"


def test_status_lifecycle_stamps_timestamps(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        case_id = app.dbs().query(GrievanceCase).one().id
    client.post(f"/admin/grievances/{case_id}/status", data={"status": "acknowledged"})
    client.post(
        f"/admin/grievances/{case_id}/status",
        data={"status": "response_provided", "response_method": "phone"},
    )
    # Closure now requires a reviewer and completed review fields.
    reviewer_id = _make_reviewer(app, case_id)
    client.post(f"/admin/grievances/{case_id}/assign",
               data={"assigned_reviewer_id": str(reviewer_id)})
    client.post(f"/admin/grievances/{case_id}/review", data={
        "findings": "Findings text.",
        "resolution": "Resolution text.",
        "guest_facing_response": "Guest response text.",
    })
    client.post(f"/admin/grievances/{case_id}/status", data={"status": "closed"})
    with app.app_context():
        case = app.dbs().get(GrievanceCase, case_id)
        assert case.acknowledged_at is not None
        assert case.response_provided_at is not None
        assert case.response_method == "phone"
        assert case.closed_at is not None
    # Reopening for additional review clears closure and sets its deadline
    client.post(f"/admin/grievances/{case_id}/status", data={"status": "additional_review"})
    with app.app_context():
        case = app.dbs().get(GrievanceCase, case_id)
        assert case.closed_at is None
        assert case.additional_review_due_at is not None


def test_response_provided_stays_in_open_queue(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        case = app.dbs().query(GrievanceCase).one()
        case_id, reference = case.id, case.public_reference
    _prepare_case_for_closure(app, client, case_id)
    resp = client.get("/admin/grievances/?view=open")
    assert reference.encode() in resp.data
    client.post(f"/admin/grievances/{case_id}/status", data={"status": "closed"})
    client.get(f"/admin/grievances/{case_id}")  # drain the "Case ... closed" flash message
    resp = client.get("/admin/grievances/?view=open")
    assert reference.encode() not in resp.data


def test_assign_rejects_non_staff_reviewer(monkeypatch, tmp_path):
    from werkzeug.security import generate_password_hash

    from guestdesk.models import User

    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        db = app.dbs()
        case_id = db.query(GrievanceCase).one().id
        viewer = User(username="viewer1", role="viewer",
                      password_hash=generate_password_hash("x"), approved=True)
        db.add(viewer)
        db.commit()
        viewer_id = viewer.id
    resp = client.post(
        f"/admin/grievances/{case_id}/assign",
        data={"assigned_reviewer_id": str(viewer_id)},
        follow_redirects=True,
    )
    assert b"approved admin or editor" in resp.data
    with app.app_context():
        assert app.dbs().get(GrievanceCase, case_id).assigned_reviewer_id is None


def test_attachment_content_sniff_rejects_mislabeled_file(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    resp = client.post(
        "/admin/grievances/new",
        data={
            "source": "paper",
            "received_date": "2026-07-03",
            "name": "Guest Two",
            "description": "Handwritten complaint.",
            "attachment": (io.BytesIO(b"not really a png"), "scan.png"),
        },
        content_type="multipart/form-data",
    )
    assert b"does not appear to be valid" in resp.data
    with app.app_context():
        assert app.dbs().query(GrievanceCase).count() == 0


def test_tracker_requires_staff_role(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with app.test_client() as client:
        assert client.get("/admin/grievances/").status_code == 403
        assert client.get("/admin/grievances/new").status_code == 403


def test_archive_and_restore_case(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Test grievance.", "name": "G"})
    with app.app_context():
        case = app.dbs().query(GrievanceCase).one()
        case_id, reference = case.id, case.public_reference

    resp = client.post(f"/admin/grievances/{case_id}/archive", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        case = app.dbs().get(GrievanceCase, case_id)
        assert case.archived_at is not None
    # hidden from working views, visible in the Archived view
    for view in ("open", "all"):
        assert reference.encode() not in client.get(f"/admin/grievances/?view={view}").data
    assert reference.encode() in client.get("/admin/grievances/?view=archived").data
    # archiving twice is a no-op
    resp = client.post(f"/admin/grievances/{case_id}/archive", follow_redirects=True)
    assert b"already archived" in resp.data

    resp = client.post(f"/admin/grievances/{case_id}/restore", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        db = app.dbs()
        case = db.get(GrievanceCase, case_id)
        assert case.archived_at is None and case.archived_by_user_id is None
        types = [e.event_type for e in db.query(GrievanceEvent).filter_by(case_id=case_id).all()]
        assert "archived" in types and "restored" in types
    assert reference.encode() in client.get("/admin/grievances/?view=open").data


def test_ensure_case_columns_migrates_old_schema(tmp_path):
    import sqlite3

    from sqlalchemy import create_engine

    from guestdesk.grievances import ensure_case_columns

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE grievance_cases (id INTEGER PRIMARY KEY, status TEXT)")
    conn.commit()
    conn.close()
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    ensure_case_columns(engine)
    ensure_case_columns(engine)  # idempotent
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(grievance_cases)").fetchall()]
    conn.close()
    assert "archived_at" in cols and "archived_by_user_id" in cols
    assert "grievance_year" in cols and "grievance_sequence" in cols


# ---- v0.3: readable reference format ----

def test_new_reference_uses_submission_year_sequence(monkeypatch, tmp_path):
    import re

    app = _make_app(monkeypatch, tmp_path)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "First.", "name": "A"})
        guest.post("/submit/maintenance", data={"body": "Leaky sink.", "category": "Plumbing"})
        guest.post("/submit/grievance", data={"description": "Second.", "name": "B"})
    with app.app_context():
        db = app.dbs()
        cases = db.query(GrievanceCase).order_by(GrievanceCase.id).all()
        assert len(cases) == 2
        first, second = cases
        year = first.original_received_at.year
        # global submission id stays global (maintenance took an id in between)
        assert second.submission_id == first.submission_id + 2
        # grievance sequence increments only for grievances
        assert (first.grievance_year, first.grievance_sequence) == (year, 1)
        assert (second.grievance_year, second.grievance_sequence) == (year, 2)
        assert first.public_reference == f"GRV-{first.submission_id}-{year}-0001"
        assert second.public_reference == f"GRV-{second.submission_id}-{year}-0002"
        assert re.fullmatch(r"GRV-\d+-\d{4}-\d{4}", first.public_reference)


def test_legacy_references_are_not_renamed_and_stay_searchable(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Old-style.", "name": "Legacy Guest"})
    legacy_ref = None
    with app.app_context():
        db = app.dbs()
        case = db.query(GrievanceCase).one()
        # simulate a pre-v0.3 case
        legacy_ref = f"GRV-{case.submission_id}-2026-1783189360"
        case.public_reference = legacy_ref
        case.grievance_year = None
        case.grievance_sequence = None
        db.commit()
    client = _admin_client(app)
    body = client.get(f"/admin/grievances/?view=all&q={legacy_ref}").data
    assert legacy_ref.encode() in body
    body = client.get("/admin/grievances/?view=all&q=1783189360").data
    assert legacy_ref.encode() in body


def test_search_by_grievance_sequence(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "First.", "name": "A"})
        guest.post("/submit/grievance", data={"description": "Second.", "name": "B"})
    with app.app_context():
        refs = [c.public_reference for c in
                app.dbs().query(GrievanceCase).order_by(GrievanceCase.id).all()]
    client = _admin_client(app)
    body = client.get("/admin/grievances/?view=all&q=0002").data
    assert refs[1].encode() in body and refs[0].encode() not in body
    body = client.get("/admin/grievances/?view=all&q=2").data
    assert refs[1].encode() in body


# ---- v0.3: grievance search ----

def _search_fixture(app):
    """Two cases with distinct fields for search assertions."""
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={
            "description": "First complaint.", "name": "Alice Smith",
            "phone": "555-0101", "email": "alice@example.org",
            "staff_involved": "Marcus Vole",
        })
        guest.post("/submit/grievance", data={
            "description": "Second complaint.", "name": "Bob Jones",
            "phone": "555-0202", "email": "bob@example.org",
            "staff_involved": "Dana Reed",
        })
    with app.app_context():
        cases = app.dbs().query(GrievanceCase).order_by(GrievanceCase.id).all()
        return [(c.id, c.public_reference, c.submission_id) for c in cases]


def test_search_by_reference_name_contact_staff_and_submission_id(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    (id1, ref1, sid1), (id2, ref2, sid2) = _search_fixture(app)
    client = _admin_client(app)

    def results(q, view="all"):
        return client.get(f"/admin/grievances/?view={view}&q={q}").data

    # full reference
    body = results(ref1)
    assert ref1.encode() in body and ref2.encode() not in body
    # complainant name (case-insensitive partial)
    body = results("smith")
    assert ref1.encode() in body and ref2.encode() not in body
    # contact info
    body = results("bob@example.org")
    assert ref2.encode() in body and ref1.encode() not in body
    # staff involved
    body = results("Vole")
    assert ref1.encode() in body and ref2.encode() not in body
    # submission id
    body = results(str(sid2))
    assert ref2.encode() in body
    # no match
    body = results("zebra")
    assert ref1.encode() not in body and ref2.encode() not in body


def test_search_combines_with_status_filters(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    (id1, ref1, _), (id2, ref2, _) = _search_fixture(app)
    client = _admin_client(app)
    _prepare_case_for_closure(app, client, id1)
    client.post(f"/admin/grievances/{id1}/status", data={"status": "closed"})
    client.get(f"/admin/grievances/{id1}")  # drain the "Case ... closed" flash message
    # Alice's case is closed: name search in open view finds nothing
    body = client.get("/admin/grievances/?view=open&q=smith").data
    assert ref1.encode() not in body
    body = client.get("/admin/grievances/?view=closed&q=smith").data
    assert ref1.encode() in body
    # Bob's stays open
    body = client.get("/admin/grievances/?view=open&q=jones").data
    assert ref2.encode() in body


# ---- v0.2: generated PDFs and intake notification safety ----

def test_public_grievance_attaches_generated_pdf(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    _enable_grievance_pdf(app, tmp_path)
    with app.test_client() as client:
        client.post("/submit/grievance", data={
            "description": "Documented complaint.",
            "name": "Guest One",
            "staff_involved": "J. Doe",
        })
    with app.app_context():
        db = app.dbs()
        case = db.query(GrievanceCase).one()
        pdf = db.query(GrievanceAttachment).filter_by(
            case_id=case.id, attachment_type=GENERATED_PDF_TYPE).one()
        text = _pdf_text(pdf.storage_path)
        assert case.public_reference in text
        assert "Source: Guest digital form" in text
        # layout fields rendered -> same template/layout as the public form
        assert "Guest One" in text and "J. Doe" in text
        # the layout prints the reference; the stamp must not double-print it
        assert "Reference:" not in text
        assert text.count(case.public_reference) == 1


def _staff_entry(client, source, attachment=None):
    data = {
        "source": source,
        "received_date": "2026-07-03",
        "received_time": "13:15",
        "name": "Guest Two",
        "description": "Complaint as received.",
    }
    if attachment is not None:
        data["attachment"] = attachment
    return client.post("/admin/grievances/new", data=data,
                       content_type="multipart/form-data", follow_redirects=True)


def test_staff_sources_attach_standard_pdf(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    _enable_grievance_pdf(app, tmp_path)
    _capture_staff_mail(monkeypatch, app)
    client = _admin_client(app)
    for source, label, attachment in [
        ("paper", "Paper / handwritten", (io.BytesIO(PNG_BYTES), "scan.png")),
        ("verbal", "Verbal", (io.BytesIO(PDF_BYTES), "notes.pdf")),
        ("staff_assisted", "Staff-assisted digital", None),
    ]:
        resp = _staff_entry(client, source, attachment)
        assert resp.status_code == 200, source
        with app.app_context():
            db = app.dbs()
            case = db.query(GrievanceCase).filter_by(source=source).one()
            pdf = db.query(GrievanceAttachment).filter_by(
                case_id=case.id, attachment_type=GENERATED_PDF_TYPE).one()
            text = _pdf_text(pdf.storage_path)
            assert f"Source: {label}" in text, source
            assert "Received: 07/03/2026 1:15 PM" in text, source
            # body comes from the shared public layout, not a staff-only report
            assert "Guest Two" in text and "Complaint as received." in text


def test_staff_intake_notification_only_contains_generated_pdf(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    _enable_grievance_pdf(app, tmp_path)
    sent = _capture_staff_mail(monkeypatch, app)
    client = _admin_client(app)
    _staff_entry(client, "paper", (io.BytesIO(PNG_BYTES), "handwritten-original.png"))
    assert len(sent) == 1
    mail = sent[0]
    assert mail["to"] == ["reviewers@example.org"]
    assert "staff" in mail["subject"].lower() or "staff" in mail["body"].lower()
    assert "Source: Paper / handwritten" in mail["body"]
    assert "not attached to this email" in mail["body"]
    attachments = mail.get("attachments") or []
    assert len(attachments) == 1
    mime, name, payload = attachments[0]
    assert mime == "application/pdf" and name.startswith("GRV-")
    # The human upload must never appear in the email, by name or by content
    assert all("handwritten-original" not in a[1] for a in attachments)
    assert all(PNG_BYTES not in a[2] for a in attachments)


def test_notification_sent_without_pdf_when_unconfigured(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)  # no FormPDFConfig bound
    sent = _capture_staff_mail(monkeypatch, app)
    client = _admin_client(app)
    _staff_entry(client, "verbal", (io.BytesIO(PDF_BYTES), "notes.pdf"))
    assert len(sent) == 1
    assert sent[0].get("attachments") is None
    assert "No grievance PDF template is configured" in sent[0]["body"]
    with app.app_context():
        db = app.dbs()
        case = db.query(GrievanceCase).one()
        assert db.query(GrievanceAttachment).filter_by(
            case_id=case.id, attachment_type=GENERATED_PDF_TYPE).count() == 0


def test_stamp_includes_reference_when_layout_lacks_id(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    _enable_grievance_pdf(app, tmp_path)
    with app.app_context():
        db = app.dbs()
        cfg = db.query(FormPDFConfig).filter_by(form_key="grievance").one()
        layout = json.loads(cfg.layout_json)
        layout.pop("id", None)
        cfg.layout_json = json.dumps(layout)
        db.commit()
    with app.test_client() as client:
        client.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        db = app.dbs()
        case = db.query(GrievanceCase).one()
        pdf = db.query(GrievanceAttachment).filter_by(
            case_id=case.id, attachment_type=GENERATED_PDF_TYPE).one()
        text = _pdf_text(pdf.storage_path)
        assert f"Reference: {case.public_reference}" in text


def test_upload_route_rejects_reserved_attachment_type(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        case_id = app.dbs().query(GrievanceCase).one().id
    resp = client.post(
        f"/admin/grievances/{case_id}/attachments",
        data={
            "attachment": (io.BytesIO(PDF_BYTES), "fake-system.pdf"),
            "attachment_type": GENERATED_PDF_TYPE,
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"reserved" in resp.data
    with app.app_context():
        assert app.dbs().query(GrievanceAttachment).count() == 0


def test_pdf_backfill_script_idempotent(monkeypatch, tmp_path):
    import os
    import subprocess
    import sys

    app = _make_app(monkeypatch, tmp_path)
    _enable_grievance_pdf(app, tmp_path)
    # Simulate a pre-v0.2 case: created directly, no PDF attachment
    with app.app_context():
        db = app.dbs()
        sub = Submission(kind="grievance", body="Old complaint.", contact_name="Guest Old")
        db.add(sub)
        db.flush()
        from guestdesk.grievances import create_case_for_submission
        case = create_case_for_submission(db, sub, source="guest_digital", actor_label="backfill")
        db.commit()
        case_id = case.id

    script = Path(__file__).resolve().parents[2] / "guestdesk" / "scripts" / "backfill_grievance_case_pdfs.py"
    env = dict(os.environ, GUESTDESK_DATA_DIR=str(tmp_path), PDF_OUTPUT_ROOT=str(tmp_path / "pdf-out"))
    run = lambda *extra: subprocess.run(
        [sys.executable, str(script), *extra],
        capture_output=True, text=True, env=env,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    out = run()
    assert "1 case(s) missing a generated PDF" in out.stdout, out.stdout + out.stderr
    out = run("--apply")
    assert "RENDERED PDF" in out.stdout, out.stdout + out.stderr
    out = run()
    assert "0 case(s) missing a generated PDF" in out.stdout, out.stdout
    with app.app_context():
        db = app.dbs()
        pdf = db.query(GrievanceAttachment).filter_by(
            case_id=case_id, attachment_type=GENERATED_PDF_TYPE).one()
        text = _pdf_text(pdf.storage_path)
        assert "Guest Old" in text and "Source: Guest digital form" in text


# ---------- Autosave, atomic status saves, response-method dropdown ----------

def _simple_case(app):
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        return app.dbs().query(GrievanceCase).one().id


def test_review_autosave_returns_json_and_logs_once(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    case_id = _simple_case(app)
    resp = client.post(
        f"/admin/grievances/{case_id}/review",
        data={"findings": "Spoke with staff.", "resolution": "Retraining.",
              "guest_facing_response": "We addressed your concern."},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert set(body["changed"]) == {"findings", "resolution", "guest_facing_response"}
    # identical resubmission changes nothing and logs no timeline event
    resp = client.post(
        f"/admin/grievances/{case_id}/review",
        data={"findings": "Spoke with staff.", "resolution": "Retraining.",
              "guest_facing_response": "We addressed your concern."},
        headers={"Accept": "application/json"},
    )
    assert resp.get_json() == {"ok": True, "changed": []}
    with app.app_context():
        db = app.dbs()
        case = db.get(GrievanceCase, case_id)
        assert case.findings == "Spoke with staff."
        events = db.query(GrievanceEvent).filter_by(
            case_id=case_id, event_type="review_updated").all()
        assert len(events) == 1


def test_review_no_longer_accepts_closure_notes(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    case_id = _simple_case(app)
    client.post(f"/admin/grievances/{case_id}/review",
                data={"closure_notes": "Should be ignored."})
    with app.app_context():
        assert app.dbs().get(GrievanceCase, case_id).closure_notes is None
    # the input is gone from the form too
    assert b'name="closure_notes"' not in client.get(f"/admin/grievances/{case_id}").data


def test_legacy_closure_notes_still_displayed(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    case_id = _simple_case(app)
    with app.app_context():
        db = app.dbs()
        db.get(GrievanceCase, case_id).closure_notes = "Old closure note text."
        db.commit()
    body = client.get(f"/admin/grievances/{case_id}").data
    assert b"Legacy closure note" in body
    assert b"Old closure note text." in body


def test_status_change_applies_review_fields_atomically(monkeypatch, tmp_path):
    # Text riding along with the close request must be applied before closure
    # validation runs and must appear in the frozen report, so closing right
    # after typing never loses or ignores the newest text.
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    case_id = _simple_case(app)
    reviewer_id = _make_reviewer(app, case_id)
    client.post(f"/admin/grievances/{case_id}/assign",
               data={"assigned_reviewer_id": str(reviewer_id)})
    client.post(
        f"/admin/grievances/{case_id}/status",
        data={"status": "closed", "response_method": "email",
              "findings": "Final findings.", "resolution": "Final resolution.",
              "guest_facing_response": "Final response."},
    )
    with app.app_context():
        db = app.dbs()
        case = db.get(GrievanceCase, case_id)
        assert case.status == "closed"
        assert case.findings == "Final findings."
        assert case.guest_facing_response == "Final response."
        att = db.query(GrievanceAttachment).filter_by(
            case_id=case_id, attachment_type=CLOSURE_REPORT_TYPE).one()
        report_path = att.storage_path
        types = [e.event_type for e in db.query(GrievanceEvent).filter_by(case_id=case_id)]
        assert "review_updated" in types and "status_changed" in types
    text = _pdf_text(report_path)
    assert "Final findings." in text and "Final response." in text


def test_rejected_status_change_still_saves_review_text(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    case_id = _simple_case(app)
    # closure blocked (no reviewer/response), but the typed text must survive
    resp = client.post(
        f"/admin/grievances/{case_id}/status",
        data={"status": "closed", "findings": "Typed just before closing."},
        follow_redirects=True,
    )
    assert b"cannot be closed yet" in resp.data
    with app.app_context():
        case = app.dbs().get(GrievanceCase, case_id)
        assert case.status == "received"
        assert case.findings == "Typed just before closing."


def test_response_method_required_for_response_statuses(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    case_id = _simple_case(app)
    resp = client.post(f"/admin/grievances/{case_id}/status",
                       data={"status": "response_provided"}, follow_redirects=True)
    assert b"record how the response was provided" in resp.data
    with app.app_context():
        case = app.dbs().get(GrievanceCase, case_id)
        assert case.status == "received" and case.response_provided_at is None
    # not required for the early lifecycle statuses
    client.post(f"/admin/grievances/{case_id}/status", data={"status": "acknowledged"})
    with app.app_context():
        assert app.dbs().get(GrievanceCase, case_id).status == "acknowledged"


def test_response_method_rejects_values_outside_dropdown(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    case_id = _simple_case(app)
    resp = client.post(
        f"/admin/grievances/{case_id}/status",
        data={"status": "response_provided", "response_method": "carrier pigeon"},
        follow_redirects=True,
    )
    assert b"choose a response method" in resp.data
    with app.app_context():
        case = app.dbs().get(GrievanceCase, case_id)
        assert case.status == "received" and case.response_method is None


def test_other_response_method_stores_detail(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    case_id = _simple_case(app)
    client.post(
        f"/admin/grievances/{case_id}/status",
        data={"status": "response_provided", "response_method": "other",
              "response_method_other": "Carrier pigeon"},
    )
    with app.app_context():
        assert app.dbs().get(GrievanceCase, case_id).response_method == "Other: Carrier pigeon"
    body = client.get(f"/admin/grievances/{case_id}").data
    assert b"Other: Carrier pigeon" in body


def test_normalize_response_method_handles_legacy_values():
    from guestdesk.grievances import normalize_response_method, response_method_label

    assert normalize_response_method(None) == ("", "")
    assert normalize_response_method("phone") == ("phone", "")
    assert normalize_response_method("in person") == ("in_person", "")
    assert normalize_response_method("Other: Carrier pigeon") == ("other", "Carrier pigeon")
    assert normalize_response_method("smoke signals") == ("other", "smoke signals")
    assert response_method_label("in_person") == "In person"
    assert response_method_label("Other: Carrier pigeon") == "Other: Carrier pigeon"


def test_closed_case_review_autosave_gets_conflict(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    case_id = _simple_case(app)
    _prepare_case_for_closure(app, client, case_id)
    client.post(f"/admin/grievances/{case_id}/status", data={"status": "closed"})
    resp = client.post(
        f"/admin/grievances/{case_id}/review",
        data={"findings": "Sneaky edit after closure."},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 409
    assert resp.get_json()["ok"] is False
    with app.app_context():
        assert app.dbs().get(GrievanceCase, case_id).findings != "Sneaky edit after closure."


# ---------- Closure workflow ----------

def _make_reviewer(app, suffix):
    from werkzeug.security import generate_password_hash

    from guestdesk.models import User

    with app.app_context():
        db = app.dbs()
        reviewer = User(username=f"reviewer_{suffix}", role="editor",
                        password_hash=generate_password_hash("x"), approved=True)
        db.add(reviewer)
        db.commit()
        return reviewer.id


def _prepare_case_for_closure(app, client, case_id, **field_overrides):
    """Assign a reviewer, record response, and fill in all closure fields
    (but do not close). Returns the reviewer's user id."""
    reviewer_id = _make_reviewer(app, case_id)
    client.post(f"/admin/grievances/{case_id}/assign",
               data={"assigned_reviewer_id": str(reviewer_id)})
    client.post(f"/admin/grievances/{case_id}/status",
               data={"status": "response_provided", "response_method": "in_person"})
    fields = {
        "findings": "Investigated the claim thoroughly.",
        "resolution": "Corrected the underlying issue.",
        "guest_facing_response": "We addressed your concern and apologize for the inconvenience.",
    }
    fields.update(field_overrides)
    client.post(f"/admin/grievances/{case_id}/review", data=fields)
    return reviewer_id


def test_response_provided_backfills_acknowledgement(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        case_id = app.dbs().query(GrievanceCase).one().id
    client.post(f"/admin/grievances/{case_id}/status",
               data={"status": "response_provided", "response_method": "in_person"})
    with app.app_context():
        case = app.dbs().get(GrievanceCase, case_id)
        assert case.status == "response_provided"
        assert case.acknowledged_at is not None
        assert case.response_provided_at is not None
        assert case.acknowledged_at == case.response_provided_at
        assert case.response_method == "in_person"


def test_additional_review_backfills_earlier_milestones(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        case_id = app.dbs().query(GrievanceCase).one().id
    client.post(f"/admin/grievances/{case_id}/status",
               data={"status": "additional_review", "response_method": "phone"})
    with app.app_context():
        case = app.dbs().get(GrievanceCase, case_id)
        assert case.status == "additional_review"
        assert case.acknowledged_at is not None
        assert case.response_provided_at is not None


def test_closure_blocked_when_required_fields_missing(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        case_id = app.dbs().query(GrievanceCase).one().id
    # No reviewer, no review fields, no response yet.
    resp = client.post(f"/admin/grievances/{case_id}/status",
                       data={"status": "closed"}, follow_redirects=True)
    assert b"cannot be closed yet" in resp.data
    assert b"Assigned reviewer is required" in resp.data
    assert b"Response method is required" in resp.data
    assert b"Findings are required" in resp.data
    assert b"Resolution is required" in resp.data
    assert b"Guest-facing response is required" in resp.data
    assert b"Closure notes" not in resp.data
    with app.app_context():
        case = app.dbs().get(GrievanceCase, case_id)
        assert case.status != "closed"
        assert case.closed_at is None
        assert app.dbs().query(GrievanceAttachment).filter_by(
            case_id=case_id, attachment_type=CLOSURE_REPORT_TYPE).count() == 0
        assert not any(e.event_type == "closure_report_generated"
                       for e in app.dbs().query(GrievanceEvent).filter_by(case_id=case_id).all())
    root = Path(tmp_path) / "uploads" / "grievance"
    assert not any(p.name.startswith("closure-report-") for p in root.rglob("*.pdf")) if root.exists() else True


def test_closure_blocked_missing_single_fields(monkeypatch, tmp_path):
    """Each individually-required field, left blank, blocks closure on its own."""
    required = [
        ("findings", "Findings are required."),
        ("resolution", "Resolution is required."),
        ("guest_facing_response", "Guest-facing response is required."),
    ]
    for missing_field, message in required:
        app = _make_app(monkeypatch, tmp_path / missing_field)
        client = _admin_client(app)
        with app.test_client() as guest:
            guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
        with app.app_context():
            case_id = app.dbs().query(GrievanceCase).one().id
        _prepare_case_for_closure(app, client, case_id, **{missing_field: "   "})
        resp = client.post(f"/admin/grievances/{case_id}/status",
                           data={"status": "closed"}, follow_redirects=True)
        assert message.encode() in resp.data, missing_field
        with app.app_context():
            assert app.dbs().get(GrievanceCase, case_id).status != "closed", missing_field


def test_successful_closure_creates_versioned_attachment_and_events(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        case = app.dbs().query(GrievanceCase).one()
        case_id, reference = case.id, case.public_reference
    reviewer_id = _prepare_case_for_closure(app, client, case_id)
    resp = client.post(f"/admin/grievances/{case_id}/status",
                       data={"status": "closed"}, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        db = app.dbs()
        case = db.get(GrievanceCase, case_id)
        assert case.status == "closed"
        assert case.closed_at is not None
        assert case.acknowledged_at is not None
        assert case.response_provided_at is not None
        attachments = db.query(GrievanceAttachment).filter_by(
            case_id=case_id, attachment_type=CLOSURE_REPORT_TYPE).all()
        assert len(attachments) == 1
        att = attachments[0]
        assert reference in att.original_filename and "closure-1" in att.original_filename
        assert Path(att.storage_path).is_file()
        assert Path(att.storage_path).read_bytes().startswith(b"%PDF")
        events = db.query(GrievanceEvent).filter_by(case_id=case_id).all()
        assert any(e.event_type == "status_changed" and e.new_value == "closed" for e in events)
        assert any(e.event_type == "closure_report_generated" for e in events)


def test_closure_report_pdf_content(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={
            "description": "The lounge was locked during posted hours.",
            "name": "Reporter One",
        })
    with app.app_context():
        case = app.dbs().query(GrievanceCase).one()
        case_id, reference = case.id, case.public_reference
    _prepare_case_for_closure(app, client, case_id)
    client.post(f"/admin/grievances/{case_id}/status", data={"status": "closed"})
    with app.app_context():
        db = app.dbs()
        att = db.query(GrievanceAttachment).filter_by(
            case_id=case_id, attachment_type=CLOSURE_REPORT_TYPE).one()
        case = db.get(GrievanceCase, case_id)
        closer = case.closed_by.username if case.closed_by else None
    text = _pdf_text(att.storage_path)
    assert reference in text
    assert "The lounge was locked during posted hours." in text
    assert "Investigated the claim thoroughly." in text
    assert "Corrected the underlying issue." in text
    assert "We addressed your concern and apologize for the inconvenience." in text
    assert "Closure notes" not in text
    assert "Final Grievance" in text and "Case Report" in text
    if closer:
        assert closer in text


def test_closed_case_is_read_only(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        case_id = app.dbs().query(GrievanceCase).one().id
    _prepare_case_for_closure(app, client, case_id)
    client.post(f"/admin/grievances/{case_id}/status", data={"status": "closed"})
    with app.app_context():
        db = app.dbs()
        case = db.get(GrievanceCase, case_id)
        findings_before = case.findings
        notes_before = len(case.notes)
        attachments_before = len(case.attachments)
        reviewer_before = case.assigned_reviewer_id

    client.post(f"/admin/grievances/{case_id}/review", data={"findings": "Tampered findings."})
    client.post(f"/admin/grievances/{case_id}/notes", data={"body": "Should not be added."})
    client.post(f"/admin/grievances/{case_id}/assign", data={"assigned_reviewer_id": ""})
    client.post(f"/admin/grievances/{case_id}/attachments",
               data={"attachment": (io.BytesIO(PDF_BYTES), "sneaky.pdf")},
               content_type="multipart/form-data")

    with app.app_context():
        db = app.dbs()
        case = db.get(GrievanceCase, case_id)
        assert case.findings == findings_before
        assert len(case.notes) == notes_before
        assert len(case.attachments) == attachments_before
        assert case.assigned_reviewer_id == reviewer_before

    resp = client.get(f"/admin/grievances/{case_id}")
    assert b"Save Review Details" not in resp.data
    assert b"Reopen Case" in resp.data
    assert b"View Printable Report" in resp.data


def test_reopen_and_reclose_creates_new_version(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        case_id = app.dbs().query(GrievanceCase).one().id
    _prepare_case_for_closure(app, client, case_id, resolution="Original resolution text.")
    client.post(f"/admin/grievances/{case_id}/status", data={"status": "closed"})
    with app.app_context():
        db = app.dbs()
        first = db.query(GrievanceAttachment).filter_by(
            case_id=case_id, attachment_type=CLOSURE_REPORT_TYPE).one()
        first_path = first.storage_path

    client.post(f"/admin/grievances/{case_id}/status", data={"status": "in_review"})
    with app.app_context():
        case = app.dbs().get(GrievanceCase, case_id)
        assert case.status == "in_review"
        assert case.closed_at is None
        # prior closure report and outcome fields survive reopening
        assert case.resolution == "Original resolution text."
    client.post(f"/admin/grievances/{case_id}/review", data={"resolution": "Revised resolution text."})
    client.post(f"/admin/grievances/{case_id}/status", data={"status": "closed"})

    with app.app_context():
        db = app.dbs()
        reports = db.query(GrievanceAttachment).filter_by(
            case_id=case_id, attachment_type=CLOSURE_REPORT_TYPE).order_by(GrievanceAttachment.id).all()
        assert len(reports) == 2
        assert "closure-1" in reports[0].original_filename
        assert "closure-2" in reports[1].original_filename
        assert Path(reports[0].storage_path).is_file()
        assert Path(reports[1].storage_path).is_file()
        assert reports[0].storage_path == first_path

    text_1 = _pdf_text(reports[0].storage_path)
    text_2 = _pdf_text(reports[1].storage_path)
    assert "Original resolution text." in text_1
    assert "Revised resolution text." not in text_1
    assert "Revised resolution text." in text_2


def test_closure_generation_failure_rolls_back(monkeypatch, tmp_path):
    import guestdesk.grievances as grievances_module

    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        case_id = app.dbs().query(GrievanceCase).one().id
    _prepare_case_for_closure(app, client, case_id)

    def _boom(case, *, report_version):
        raise RuntimeError("rendering exploded")

    monkeypatch.setattr(grievances_module, "render_closure_report_pdf", _boom)
    resp = client.post(f"/admin/grievances/{case_id}/status",
                       data={"status": "closed"}, follow_redirects=True)
    assert b"could not be closed" in resp.data
    with app.app_context():
        db = app.dbs()
        case = db.get(GrievanceCase, case_id)
        assert case.status != "closed"
        assert case.closed_at is None
        assert db.query(GrievanceAttachment).filter_by(
            case_id=case_id, attachment_type=CLOSURE_REPORT_TYPE).count() == 0
        assert not any(e.event_type == "closure_report_generated"
                       for e in db.query(GrievanceEvent).filter_by(case_id=case_id).all())
    root = Path(tmp_path) / "uploads" / "grievance"
    assert not any(p.name.startswith("closure-report-") for p in root.rglob("*")) if root.exists() else True


def test_reserved_types_rejected_including_closure_report(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _admin_client(app)
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        case_id = app.dbs().query(GrievanceCase).one().id
    for reserved_type in (GENERATED_PDF_TYPE, CLOSURE_REPORT_TYPE):
        resp = client.post(
            f"/admin/grievances/{case_id}/attachments",
            data={
                "attachment": (io.BytesIO(PDF_BYTES), "fake-system.pdf"),
                "attachment_type": reserved_type,
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert b"reserved" in resp.data, reserved_type
    with app.app_context():
        assert app.dbs().query(GrievanceAttachment).count() == 0
