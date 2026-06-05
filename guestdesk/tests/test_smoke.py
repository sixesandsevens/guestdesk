from guestdesk.app import create_app


def test_healthz():
    app = create_app()
    with app.test_client() as client:
        resp = client.get("/_healthz")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True



def _make_test_app(monkeypatch, tmp_path):
    import guestdesk.app as app_module

    sent = []

    def fake_queue_mail(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "queue_mail", fake_queue_mail)
    app = app_module.create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        ADMIN_EMAIL="staff@example.org",
        MAINTENANCE_EMAIL_TO=["maintenance@example.org"],
        GRIEVANCE_EMAIL="grievance@example.org",
    )
    return app, sent


def test_maintenance_submitter_gets_confirmation_copy(monkeypatch, tmp_path):
    app, sent = _make_test_app(monkeypatch, tmp_path)

    with app.test_client() as client:
        resp = client.post(
            "/submit/maintenance",
            data={
                "subject": "Leaky sink",
                "body": "Water is pooling under the sink.",
                "category": "Plumbing",
                "building": "A",
                "location": "Room 101",
                "contact_name": "Guest One",
                "contact_info": "555-0100",
                "email": "guest@example.org",
            },
        )

    assert resp.status_code == 200
    assert len(sent) == 2
    confirmation = sent[1]
    assert confirmation["to"] == ["guest@example.org"]
    assert "copy of your submission" in confirmation["body"].lower()
    assert "Water is pooling under the sink." in confirmation["body"]
    assert "Leaky sink" in confirmation["body"]


def test_grievance_submitter_gets_case_confirmation(monkeypatch, tmp_path):
    app, sent = _make_test_app(monkeypatch, tmp_path)

    with app.test_client() as client:
        resp = client.post(
            "/submit/grievance",
            data={
                "subject": "Concern",
                "description": "I want this grievance documented.",
                "name": "Guest Two",
                "phone": "555-0101",
                "email": "guest-two@example.org",
                "staff_involved": "Staff Name",
            },
        )

    assert resp.status_code == 200
    assert len(sent) == 2
    confirmation = sent[1]
    assert confirmation["to"] == ["guest-two@example.org"]
    assert "GRV-" in confirmation["subject"]
    assert "I want this grievance documented." in confirmation["body"]
    assert "Staff Name" in confirmation["body"]
