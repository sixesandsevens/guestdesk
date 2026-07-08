import io

from guestdesk.models import (
    GrievanceCase,
    GrievanceAttachment,
    GrievanceEvent,
    Submission,
)
from guestdesk.grievances import add_business_days


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
            "attachment": (io.BytesIO(b"\x89PNG fake"), "scan.png"),
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


def test_tracker_requires_staff_role(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with app.test_client() as client:
        assert client.get("/admin/grievances/").status_code == 403
        assert client.get("/admin/grievances/new").status_code == 403
