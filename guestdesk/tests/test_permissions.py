from werkzeug.security import generate_password_hash

from guestdesk.models import GrievanceCase, User, UserPermission


def _make_app(monkeypatch, tmp_path):
    import guestdesk.app as app_module
    import guestdesk.display as display_module
    import guestdesk.grievances as grievances_module

    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(grievances_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(display_module, "DATA_ROOT", tmp_path / "display")
    monkeypatch.setattr(display_module, "DATA_PATH", tmp_path / "display" / "display_config.json")
    monkeypatch.setattr(display_module, "SLIDES_DIR", tmp_path / "display" / "display_slides")
    monkeypatch.setattr(app_module, "queue_mail", lambda **kwargs: None)
    monkeypatch.setattr(grievances_module, "queue_mail", lambda **kwargs: None)
    app = app_module.create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


def _user(app, username, role="viewer", perms=()):
    with app.app_context():
        db = app.dbs()
        u = User(username=username, role=role,
                 password_hash=generate_password_hash("x"), approved=True)
        db.add(u)
        db.flush()
        for key in perms:
            db.add(UserPermission(user_id=u.id, permission=key))
        db.commit()
        return u.id


def _login(app, uid):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = uid
    return client


def _make_case(app):
    with app.test_client() as guest:
        guest.post("/submit/grievance", data={"description": "Complaint.", "name": "G"})
    with app.app_context():
        return app.dbs().query(GrievanceCase).order_by(GrievanceCase.id.desc()).first().id


PROTECTED = {
    "grievances": "/admin/grievances/",
    "grievance_new": "/admin/grievances/new",
    "pdf_index": "/admin/forms/pdf",
    "email_settings": "/admin/email-settings",
    "users": "/admin/users",
    "services": "/admin/services",
    "submissions": "/admin/submissions",
    "displays": "/admin/displays",
}


def test_admin_passes_everything(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    uid = _user(app, "boss", role="admin")
    client = _login(app, uid)
    for name, url in PROTECTED.items():
        assert client.get(url).status_code == 200, name


def test_viewer_without_grants_denied_everywhere(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    uid = _user(app, "viewer1", role="viewer")
    client = _login(app, uid)
    for name, url in PROTECTED.items():
        assert client.get(url).status_code == 403, name


def test_editor_role_alone_grants_nothing(monkeypatch, tmp_path):
    # Rows are the truth: the editor role without grants has no access
    app = _make_app(monkeypatch, tmp_path)
    uid = _user(app, "editor1", role="editor")
    client = _login(app, uid)
    for name, url in PROTECTED.items():
        assert client.get(url).status_code == 403, name


def test_north_star_services_display_editor_cannot_touch_sensitive_areas(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    uid = _user(app, "editor2", role="editor", perms=[
        "services.view", "services.edit", "displays.view", "displays.edit",
    ])
    client = _login(app, uid)
    assert client.get("/admin/services").status_code == 200
    assert client.get("/admin/announcements").status_code == 200
    assert client.get("/admin/displays").status_code == 200
    for name in ("grievances", "grievance_new", "pdf_index", "email_settings", "users"):
        assert client.get(PROTECTED[name]).status_code == 403, name


def test_intake_staff_can_create_but_not_review(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    uid = _user(app, "intake1", perms=["grievances.create"])
    client = _login(app, uid)
    assert client.get("/admin/grievances/new").status_code == 200
    resp = client.post("/admin/grievances/new", data={
        "source": "staff_assisted",
        "received_date": "2026-07-03",
        "name": "Guest",
        "description": "Assisted complaint.",
    })
    # created, but redirected back to the intake form (no grievances.view)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/grievances/new")
    with app.app_context():
        case = app.dbs().query(GrievanceCase).one()
        case_id = case.id
    assert client.get("/admin/grievances/").status_code == 403
    assert client.get(f"/admin/grievances/{case_id}").status_code == 403
    assert client.post(f"/admin/grievances/{case_id}/status",
                       data={"status": "in_review"}).status_code == 403


def test_reviewer_can_work_cases_but_not_pdf_or_assign(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    uid = _user(app, "reviewer1", perms=[
        "grievances.view", "grievances.review", "grievances.attach", "grievances.close",
    ])
    case_id = _make_case(app)
    client = _login(app, uid)
    assert client.get(f"/admin/grievances/{case_id}").status_code == 200
    assert client.post(f"/admin/grievances/{case_id}/status",
                       data={"status": "in_review"}).status_code == 302
    assert client.post(f"/admin/grievances/{case_id}/notes",
                       data={"body": "Looked into it."}).status_code == 302
    assert client.post(f"/admin/grievances/{case_id}/status",
                       data={"status": "closed"}).status_code == 302
    assert client.get("/admin/forms/pdf").status_code == 403
    assert client.post(f"/admin/grievances/{case_id}/assign",
                       data={"assigned_reviewer_id": ""}).status_code == 403


def test_closing_requires_close_permission(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    uid = _user(app, "reviewer2", perms=["grievances.view", "grievances.review"])
    case_id = _make_case(app)
    client = _login(app, uid)
    assert client.post(f"/admin/grievances/{case_id}/status",
                       data={"status": "in_review"}).status_code == 302
    assert client.post(f"/admin/grievances/{case_id}/status",
                       data={"status": "closed"}).status_code == 403
    assert client.post(f"/admin/grievances/{case_id}/archive").status_code == 403


def test_reopen_requires_close_permission_and_is_hidden_without_it(monkeypatch, tmp_path):
    from werkzeug.security import generate_password_hash

    app = _make_app(monkeypatch, tmp_path)
    admin_id = _user(app, "boss", role="admin")
    case_id = _make_case(app)
    admin = _login(app, admin_id)
    with app.app_context():
        db = app.dbs()
        rev = User(username="rev1", role="editor",
                  password_hash=generate_password_hash("x"), approved=True)
        db.add(rev)
        db.commit()
        rev_id = rev.id
    admin.post(f"/admin/grievances/{case_id}/assign", data={"assigned_reviewer_id": str(rev_id)})
    admin.post(f"/admin/grievances/{case_id}/status",
              data={"status": "response_provided", "response_method": "phone"})
    admin.post(f"/admin/grievances/{case_id}/review", data={
        "findings": "f", "resolution": "r", "guest_facing_response": "g",
    })
    assert admin.post(f"/admin/grievances/{case_id}/status",
                      data={"status": "closed"}).status_code == 302
    with app.app_context():
        assert app.dbs().get(GrievanceCase, case_id).status == "closed"

    uid = _user(app, "viewer_only", perms=["grievances.view", "grievances.review"])
    client = _login(app, uid)
    resp = client.get(f"/admin/grievances/{case_id}")
    assert resp.status_code == 200
    assert b"Reopen Case" not in resp.data
    assert client.post(f"/admin/grievances/{case_id}/status",
                       data={"status": "in_review"}).status_code == 403
    with app.app_context():
        assert app.dbs().get(GrievanceCase, case_id).status == "closed"


def test_series_write_endpoints_require_services_edit(monkeypatch, tmp_path):
    # The JSON series routes must honor permission rows, not the legacy editor role
    app = _make_app(monkeypatch, tmp_path)
    editor = _user(app, "editor3", role="editor")
    client = _login(app, editor)
    assert client.put("/admin/services/series/1", json={}).status_code == 403
    assert client.patch("/admin/services/series/1", json={}).status_code == 403
    assert client.delete("/admin/services/series/1").status_code == 403

    granted = _user(app, "scheduler", perms=["services.edit"])
    client = _login(app, granted)
    # 404 (no such series), not 403: the permission gate let them through
    assert client.put("/admin/services/series/1", json={}).status_code == 404
    assert client.delete("/admin/services/series/1").status_code == 404


def test_pdf_manager_cannot_edit_email_recipients(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    uid = _user(app, "pdfmgr", perms=["pdf_forms.view", "pdf_forms.edit"])
    client = _login(app, uid)
    assert client.get("/admin/forms/pdf").status_code == 200
    assert client.get("/admin/email-settings").status_code == 403
    assert client.post("/admin/email-settings", data={}).status_code == 403


def test_only_permission_manager_can_change_permissions(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    manager = _user(app, "manager", perms=["admin.users.manage"])
    target = _user(app, "target")
    outsider = _user(app, "outsider", perms=["services.edit"])

    client = _login(app, outsider)
    assert client.get(f"/admin/users/{target}/permissions").status_code == 403
    assert client.post(f"/admin/users/{target}/permissions",
                       data={"permissions": "grievances.view"}).status_code == 403

    client = _login(app, manager)
    assert client.get(f"/admin/users/{target}/permissions").status_code == 200
    resp = client.post(f"/admin/users/{target}/permissions",
                       data={"permissions": ["grievances.view", "not.a.real.permission"]})
    assert resp.status_code == 302
    with app.app_context():
        db = app.dbs()
        keys = {r.permission for r in db.query(UserPermission).filter_by(user_id=target).all()}
        assert keys == {"grievances.view"}  # unknown keys dropped


def test_seed_script_grants_legacy_editor_permissions(monkeypatch, tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    app = _make_app(monkeypatch, tmp_path)
    _user(app, "boss", role="admin")
    editor = _user(app, "ed", role="editor")
    viewer = _user(app, "vw", role="viewer")

    script = Path(__file__).resolve().parents[2] / "guestdesk" / "scripts" / "seed_permissions.py"
    env = dict(os.environ, GUESTDESK_DATA_DIR=str(tmp_path))
    run = lambda *extra: subprocess.run(
        [sys.executable, str(script), *extra],
        capture_output=True, text=True, env=env,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    out = run()
    assert "would grant" in out.stdout, out.stdout + out.stderr
    out = run("--apply")
    assert "seeded 1 user(s)" in out.stdout, out.stdout + out.stderr
    out = run()
    assert "already seeded" in out.stdout
    with app.app_context():
        db = app.dbs()
        editor_keys = {r.permission for r in db.query(UserPermission).filter_by(user_id=editor).all()}
        assert "services.edit" in editor_keys and "submissions.view" in editor_keys
        # sensitive areas never auto-granted
        assert not any(k.startswith(("grievances.", "pdf_forms.", "settings.")) for k in editor_keys)
        assert db.query(UserPermission).filter_by(user_id=viewer).count() == 0