GuestDesk — Flask-Login Drop‑in (v4)
====================================

What this bundle does (safely, idempotently)
--------------------------------------------
- Adds Flask‑Login imports to `guestdesk/app.py` (won’t break if unused).
- Creates a safe **/admin** landing endpoint named `admin_index` inside `create_app()` (if missing).
- Fixes wrong `url_for('admin')` calls → `url_for('admin_index')`.
- Makes `next` handling in the login view safe by defaulting to `admin_index`.
- Removes the stray “Staff Login” link on the login page `templates/login.html`.
- Softens `roles_required()` (if present) to honor `session['is_admin']` and roles on `g.user`.

> This patch does **not** change your DB schema. It keeps your current auth flow
> working while making the admin landing and login UX behave correctly. We can
> layer full Flask‑Login session management on top in a later pass if you want.


How to apply
------------
Run these on the server (adjust paths if your repo root is different):

```bash
# 0) (One‑time) make sure flask-login is installed
sudo -u guestdesk -H bash -lc 'cd /opt/guestdesk && source venv/bin/activate && pip install "flask-login>=0.6.3"'

# 1) Unzip into your repo root (this zip contains /scripts and this README)
sudo unzip -o /mnt/data/guestdesk_flask_login_dropin_v4.zip -d /opt/guestdesk

# 2) Run the patcher (it’s idempotent—safe to run multiple times)
sudo /opt/guestdesk/venv/bin/python /opt/guestdesk/scripts/patch_flask_login.py /opt/guestdesk

# 3) Clear bytecode & restart the service
sudo find /opt/guestdesk/guestdesk -name "__pycache__" -type d -exec rm -rf {} +
sudo find /opt/guestdesk/guestdesk -name "*.pyc" -delete
sudo systemctl restart guestdesk
```

Verify
------
- `/admin` while not logged in → should take you to `/login?next=/admin`.
- Log in with a staff/admin account → should land on `/admin/services`.
- The extra “Staff Login” link on the login page should be gone.
- The top‑nav “Staff” item still points to `/admin` (that’s intended).

Rollback
--------
Each modified file gets a `*.bak` alongside it on first patch.
To rollback a single file:

```bash
sudo cp /opt/guestdesk/guestdesk/templates/login.html.bak /opt/guestdesk/guestdesk/templates/login.html
sudo cp /opt/guestdesk/guestdesk/app.py.bak /opt/guestdesk/guestdesk/app.py
# ...then restart
sudo systemctl restart guestdesk
```

Commit and push (optional)
--------------------------
```bash
sudo -u guestdesk -H bash -lc '
  cd /opt/guestdesk
  git checkout -b server-sync-flask-login || true
  git add -A
  git commit -m "Flask-Login drop-in: admin landing, next handling, roles_required softening"
  git push -u origin server-sync-flask-login
'
```
