import io

from werkzeug.security import generate_password_hash

from guestdesk.models import Announcement, AnnouncementImage, User, UserPermission


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


def _editor(app):
    with app.app_context():
        db = app.dbs()
        u = User(username="poster", role="editor",
                 password_hash=generate_password_hash("x"), approved=True)
        db.add(u)
        db.flush()
        db.add(UserPermission(user_id=u.id, permission="services.view"))
        db.add(UserPermission(user_id=u.id, permission="services.edit"))
        db.commit()
        return u.id


def _login(app, uid):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = uid
    return client


def _post_announcement(client, images):
    return client.post("/admin/announcements/new", data={
        "title": "Pool party",
        "body": "Bring a towel.",
        "images": images,
    }, content_type="multipart/form-data")


def test_post_announcement_with_images(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _login(app, _editor(app))
    resp = _post_announcement(client, [
        (io.BytesIO(b"png-bytes"), "flyer.png"),
        (io.BytesIO(b"jpg-bytes"), "map.jpg"),
    ])
    assert resp.status_code == 302
    with app.app_context():
        db = app.dbs()
        ann = db.query(Announcement).one()
        names = {i.stored_filename for i in ann.images}
        assert names == {"flyer.png", "map.jpg"}
        for name in names:
            assert (tmp_path / "uploads" / "announcements" / str(ann.id) / name).is_file()


def test_images_render_publicly_and_are_served(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _login(app, _editor(app))
    _post_announcement(client, [(io.BytesIO(b"png-bytes"), "flyer.png")])
    with app.app_context():
        image_id = app.dbs().query(AnnouncementImage).one().id

    guest = app.test_client()
    url = f"/announcements/image/{image_id}"
    for page in ("/", "/announcements"):
        assert url in guest.get(page).get_data(as_text=True), page
    resp = guest.get(url)
    assert resp.status_code == 200 and resp.data == b"png-bytes"
    assert guest.get("/announcements/image/9999").status_code == 404


def test_duplicate_filenames_stored_uniquely(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _login(app, _editor(app))
    _post_announcement(client, [
        (io.BytesIO(b"one"), "photo.png"),
        (io.BytesIO(b"two"), "photo.png"),
    ])
    with app.app_context():
        names = {i.stored_filename for i in app.dbs().query(AnnouncementImage).all()}
    assert names == {"photo.png", "photo_1.png"}


def test_non_image_upload_rejected(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _login(app, _editor(app))
    resp = _post_announcement(client, [(io.BytesIO(b"MZ"), "malware.exe")])
    assert resp.status_code == 200  # re-rendered form, nothing created
    with app.app_context():
        db = app.dbs()
        assert db.query(Announcement).count() == 0
        assert db.query(AnnouncementImage).count() == 0


def test_edit_updates_text_and_adds_images(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _login(app, _editor(app))
    _post_announcement(client, [])
    with app.app_context():
        ann_id = app.dbs().query(Announcement).one().id

    page = client.get(f"/admin/announcements/{ann_id}/edit").get_data(as_text=True)
    assert "Pool party" in page and "Edit Announcement" in page

    resp = client.post(f"/admin/announcements/{ann_id}/edit", data={
        "title": "Pool party (rescheduled)",
        "body": "Now on Saturday.",
        "starts_at": "2026-07-10T09:00",
        "images": [(io.BytesIO(b"late-add"), "flyer.png")],
    }, content_type="multipart/form-data")
    assert resp.status_code == 302
    with app.app_context():
        db = app.dbs()
        ann = db.get(Announcement, ann_id)
        assert ann.title == "Pool party (rescheduled)"
        assert ann.starts_at.strftime("%Y-%m-%dT%H:%M") == "2026-07-10T09:00"
        assert [i.stored_filename for i in ann.images] == ["flyer.png"]
    assert (tmp_path / "uploads" / "announcements" / str(ann_id) / "flyer.png").is_file()


def test_remove_single_image(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _login(app, _editor(app))
    _post_announcement(client, [
        (io.BytesIO(b"one"), "keep.png"),
        (io.BytesIO(b"two"), "drop.png"),
    ])
    with app.app_context():
        db = app.dbs()
        ann_id = db.query(Announcement).one().id
        drop_id = db.query(AnnouncementImage).filter_by(stored_filename="drop.png").one().id

    resp = client.post(f"/admin/announcements/{ann_id}/images/{drop_id}/delete")
    assert resp.status_code == 302
    with app.app_context():
        names = [i.stored_filename for i in app.dbs().query(AnnouncementImage).all()]
    assert names == ["keep.png"]
    ann_dir = tmp_path / "uploads" / "announcements" / str(ann_id)
    assert (ann_dir / "keep.png").is_file() and not (ann_dir / "drop.png").exists()
    # image id from another announcement's namespace 404s
    assert client.post(f"/admin/announcements/{ann_id + 1}/images/{drop_id}/delete").status_code == 404


def test_image_storage_failure_still_posts_announcement(monkeypatch, tmp_path):
    # A broken uploads dir must not 500 the post; announcement lands with a warning
    app = _make_app(monkeypatch, tmp_path)
    (tmp_path / "uploads").write_text("not a directory")
    client = _login(app, _editor(app))
    resp = _post_announcement(client, [(io.BytesIO(b"png"), "flyer.png")])
    assert resp.status_code == 302
    follow = client.get("/admin/announcements").get_data(as_text=True)
    assert "images could not be saved" in follow
    with app.app_context():
        db = app.dbs()
        assert db.query(Announcement).count() == 1
        assert db.query(AnnouncementImage).count() == 0


def test_delete_announcement_removes_images(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    client = _login(app, _editor(app))
    _post_announcement(client, [(io.BytesIO(b"png-bytes"), "flyer.png")])
    with app.app_context():
        ann_id = app.dbs().query(Announcement).one().id
    assert client.post(f"/admin/announcements/{ann_id}/delete").status_code == 302
    with app.app_context():
        assert app.dbs().query(AnnouncementImage).count() == 0
    assert not (tmp_path / "uploads" / "announcements" / str(ann_id)).exists()
