from guestdesk.app import create_app


def test_healthz():
    app = create_app()
    with app.test_client() as client:
        resp = client.get("/_healthz")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True
