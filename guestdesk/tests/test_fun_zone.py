import json

from werkzeug.security import generate_password_hash

from guestdesk.models import Joke, User, UserPermission


def _make_app(monkeypatch, tmp_path):
    import guestdesk.app as app_module

    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "queue_mail", lambda **kwargs: None)
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


def _disable_all_jokes(app):
    with app.app_context():
        db = app.dbs()
        for j in db.query(Joke).all():
            j.enabled = False
        db.commit()


def _replace_jokes(app, jokes):
    """Disable every seeded joke and add exactly the given (setup, punchline) rows, enabled."""
    with app.app_context():
        db = app.dbs()
        for j in db.query(Joke).all():
            j.enabled = False
        ids = []
        for setup, punchline in jokes:
            j = Joke(setup=setup, punchline=punchline, enabled=True)
            db.add(j)
            db.flush()
            ids.append(j.id)
        db.commit()
        return ids


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(calls, fail=False):
    def _inner(url, timeout=None):
        calls.append(url)
        if "official-joke-api" in url:
            raise AssertionError("Official Joke API must never be called")
        if fail:
            raise OSError("network unavailable")
        if "quotable.io" in url:
            return _FakeResponse({"content": "Test quote", "author": "Tester"})
        if "opentdb.com" in url:
            return _FakeResponse({"results": [{"question": "Q?", "correct_answer": "A"}]})
        raise RuntimeError(f"unexpected url requested: {url}")
    return _inner


def test_fun_renders_with_enabled_local_jokes(monkeypatch, tmp_path):
    import guestdesk.app as app_module
    app = _make_app(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(app_module.urlreq, "urlopen", _fake_urlopen(calls))

    client = app.test_client()
    resp = client.get("/fun")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Test quote" in html
    assert "Q?" in html


def test_fun_never_calls_official_joke_api(monkeypatch, tmp_path):
    import guestdesk.app as app_module
    app = _make_app(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(app_module.urlreq, "urlopen", _fake_urlopen(calls))

    client = app.test_client()
    for _ in range(5):
        assert client.get("/fun").status_code == 200

    assert calls, "expected quote/trivia fetch attempts"
    assert not any("official-joke-api" in c for c in calls)


def test_quote_and_trivia_failures_do_not_break_fun(monkeypatch, tmp_path):
    import guestdesk.app as app_module
    app = _make_app(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(app_module.urlreq, "urlopen", _fake_urlopen(calls, fail=True))

    client = app.test_client()
    resp = client.get("/fun")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Falls back to the offline quote/trivia lists.
    assert "One day at a time." in html or "You matter" in html or "Small steps" in html or "best time to start" in html


def test_only_enabled_jokes_are_selected(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    enabled_ids = _replace_jokes(app, [
        ("Enabled one setup", "Enabled one punchline"),
        ("Enabled two setup", "Enabled two punchline"),
    ])
    with app.app_context():
        db = app.dbs()
        disabled = Joke(setup="Disabled setup", punchline="Disabled punchline", enabled=False)
        db.add(disabled)
        db.commit()
        disabled_id = disabled.id

    with app.app_context():
        for _ in range(30):
            joke_id, text = app._pick_local_joke()
            assert joke_id != disabled_id
            assert joke_id in enabled_ids
            assert "Disabled" not in text


OFFLINE_JOKES = [
    "Why did the server go to therapy? Too many unresolved requests.",
    "I told the electrician a joke. He was shocked.",
    "I tried to catch some fog. I mist.",
    "Parallel lines have so much in common. It’s a shame they’ll never meet.",
]


def test_fallback_when_no_enabled_jokes(monkeypatch, tmp_path):
    import guestdesk.app as app_module
    app = _make_app(monkeypatch, tmp_path)
    _disable_all_jokes(app)
    calls = []
    monkeypatch.setattr(app_module.urlreq, "urlopen", _fake_urlopen(calls))

    client = app.test_client()
    resp = client.get("/fun")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert any(j in html for j in OFFLINE_JOKES)


def test_joke_text_is_html_escaped(monkeypatch, tmp_path):
    import guestdesk.app as app_module
    app = _make_app(monkeypatch, tmp_path)
    _replace_jokes(app, [("<script>alert(1)</script>", "gotcha")])
    calls = []
    monkeypatch.setattr(app_module.urlreq, "urlopen", _fake_urlopen(calls))

    client = app.test_client()
    resp = client.get("/fun")
    html = resp.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_joke_selection_does_not_immediately_repeat(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    _replace_jokes(app, [
        ("A setup", "A punch"),
        ("B setup", "B punch"),
        ("C setup", "C punch"),
    ])
    with app.app_context():
        previous_id, _ = app._pick_local_joke()
        for _ in range(20):
            current_id, _ = app._pick_local_joke()
            assert current_id != previous_id
            previous_id = current_id


def test_admin_jokes_requires_authorization(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    anon = app.test_client()
    assert anon.get("/admin/jokes").status_code in (401, 403)

    unauthorized = _user(app, "editor1", role="editor", perms=["services.edit"])
    client = _login(app, unauthorized)
    assert client.get("/admin/jokes").status_code == 403
    assert client.get("/admin/jokes/new").status_code == 403

    authorized = _user(app, "joker", perms=["funzone.view", "funzone.edit"])
    client = _login(app, authorized)
    assert client.get("/admin/jokes").status_code == 200
    assert client.get("/admin/jokes/new").status_code == 200


def test_admin_can_create_edit_disable_and_delete_a_joke(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    uid = _user(app, "joker2", perms=["funzone.view", "funzone.edit"])
    client = _login(app, uid)

    resp = client.post("/admin/jokes/new", data={
        "setup": "Why did the guest smile?",
        "punchline": "GuestDesk made it easy.",
        "source": "Staff",
        "enabled": "1",
    })
    assert resp.status_code == 302
    with app.app_context():
        db = app.dbs()
        j = db.query(Joke).filter_by(setup="Why did the guest smile?").one()
        jid = j.id
        assert j.enabled is True
        assert j.source == "Staff"

    resp = client.post(f"/admin/jokes/{jid}/edit", data={
        "setup": "Why did the guest smile? (updated)",
        "punchline": "GuestDesk made it even easier.",
        "source": "Staff",
        # enabled omitted -> disables the joke
    })
    assert resp.status_code == 302
    with app.app_context():
        db = app.dbs()
        j = db.get(Joke, jid)
        assert j.setup == "Why did the guest smile? (updated)"
        assert j.enabled is False

    resp = client.post(f"/admin/jokes/{jid}/delete")
    assert resp.status_code == 302
    with app.app_context():
        assert app.dbs().get(Joke, jid) is None


def test_blank_joke_submission_is_rejected(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    uid = _user(app, "joker3", perms=["funzone.view", "funzone.edit"])
    client = _login(app, uid)

    with app.app_context():
        before = app.dbs().query(Joke).count()

    resp = client.post("/admin/jokes/new", data={
        "setup": "   ",
        "punchline": "",
        "enabled": "1",
    })
    assert resp.status_code == 200  # re-rendered form, nothing created

    with app.app_context():
        after = app.dbs().query(Joke).count()
    assert after == before
