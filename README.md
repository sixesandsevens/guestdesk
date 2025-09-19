GuestDesk
========

Overview
--------
GuestDesk is a Flask-based web application that helps service providers
publish a public-facing resource hub while managing internal updates. It
exposes an accessible schedule of services, collects several categories of
feedback (maintenance, grievances, suggestions, questions), and provides a
staff-only administration console. This repository contains everything needed
to run the production stack currently deployed for GRACE Marketplace, but it
is adaptable to other sites with similar needs.

Key Features
------------
- **Public guest experience**: localized navigation (English/Spanish), service
  listings, announcements, interactive fun zone, feedback/reporting forms, and
  health check endpoint (`/_healthz`).
- **Service scheduling**: recurring and ad-hoc slots, with ICS export via
  `/calendar.ics`, plus admin tools to maintain program slots and overrides.
- **Submission workflow**: form submissions persisted to SQLite, optional
  photo uploads (saved under the data directory), and email notifications per
  category.
- **Audit logging**: append-only JSON audit log capturing admin actions (service
  creation, slot edits, settings changes, user management). A basic log viewer
  is available at `/admin/audit` for admin roles.
- **Security hardening**: CSRF protection (Flask-WTF), rate limiting
  (Flask-Limiter), secure headers (Flask-Talisman), request IDs for diagnostics,
  audit logging, and configurable secure-cookie enforcement.
- **Background processing**: Redis + RQ queue for asynchronous email delivery.
- **Basic analytics**: optional anonymized visitor analytics (see
  `analytics.py`) and aggregated admin dashboard counters.
- **Language aware templates**: translation helper `t()` backed by session
  language selection.

Repository Layout
-----------------
```
analytics.py          request analytics blueprint
antispam.py          redis-backed idempotency helper for forms
app.py               Flask factory, routes, security wiring
audit.py             file-based audit logger (JSON lines)
config.py            environment-driven configuration class
forms/               template fragments for public submission forms
ics.py               ICS calendar export blueprint
mailer.py            SMTP helpers + RQ-ready queue_mail() API
models.py            SQLAlchemy models (Service, Submission, etc.)
pdf_config.py/.py    legacy PDF form helpers (still used for email attachments)
requirements.txt     pip dependencies
rq_worker.py         background worker entry point
services_calendar.py service + override schedule expansion routines
static/              CSS, JS, image assets
templates/           Jinja2 templates (public + admin UI)
tests/               minimal smoke test for `/_healthz`
wsgi.py              convenience entry point for WSGI servers
```

System Requirements
-------------------
* Python 3.12+
* Redis (for RQ queue and idempotency cache)
* SQLite 3 (default production database)
* Optional: nginx or another reverse proxy terminating TLS

Quick Start (Development)
-------------------------
```
git clone <repo-url>
cd guestdesk
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Set minimal environment (adjust paths as desired)
export FLASK_ENV=development
export SECRET_KEY=dev-secret
export ADMIN_PASSWORD=changeme
export GUESTDESK_DATA_DIR=$(pwd)/devdata
mkdir -p "$GUESTDESK_DATA_DIR"

# Initialize the app (creates SQLite DB on first run)
python - <<'PY'
from guestdesk.app import create_app
app = create_app()
with app.app_context():
    print('database at', app.config['DATA_DIR'])
PY

# Run the Flask dev server
flask --app guestdesk.app:create_app --debug run
```

The admin console lives at `/admin`. The bootstrap process creates an
auto-approved admin password (default value `changeme`). Update this immediately
in production via environment variables or the admin UI.

Environment Configuration
-------------------------
Common environment variables (see `config.py` and `app.py` for full list):

* **SECRET_KEY** – required in production; controls session signing.
* **ADMIN_PASSWORD** – default password for the bootstrap admin account if the
  users table is empty.
* **GUESTDESK_DATA_DIR** – root for persistent data (SQLite DB, uploads).
  Defaults to `/var/lib/guestdesk`.
* **GUESTDESK_FORCE_SECURE_COOKIES** – set to `1` (default) to enforce HTTPS-only
  cookies via Talisman; set to `0` for HTTP environments (development or when
  behind an insecure proxy).
* **GUESTDESK_AUDIT_LOG** – path to the JSON audit log file
  (default `/var/log/guestdesk/audit.log`). Ensure the service account has write
  access.
* **GUESTDESK_VERSION** – optional string exposed at `/_healthz`.
* **MAIL_* / SMTP_* / EMAIL_* variables** – see `config.py` for all supported
  names. Configure SMTP credentials or disable mail by setting
  `EMAIL_ENABLED=0` and `MAIL_ENABLED=0`.
* **REDIS_URL** – used by `antispam.py`, `task_queue.py`, and `rq_worker.py`
  (default `redis://localhost:6379/0`).
* **GUESTDESK_MAX_UPLOAD_MB** / `GUESTDESK_MAX_UPLOAD_BYTES` – optional file size
  limits for photo uploads.

Admin & Authentication
----------------------
* Authentication is session-based using the `users` table in SQLite. The first
  time the app starts it will create a default admin record using
  `ADMIN_PASSWORD`.
* Role-based access:
  - `admin` – full access, including user management, audit log, email settings.
  - `editor` – manage services, slots, announcements, but not user settings.
  - `viewer` – read-only access to certain admin views.
* The `roles_required()` decorator in `app.py` enforces these roles. A legacy
  single-password admin session (`session['admin']`) still bypasses checks for
  emergency access.

Email & Background Jobs
-----------------------
* `mailer.queue_mail()` enqueues emails onto the default RQ queue. If the queue
  is unavailable, it falls back to synchronous delivery.
* Run a worker with the bundled script:
  `python -m rq_worker` (or the systemd unit shown below).
* Attachments: maintenance photos and generated PDFs are attached to outgoing
  messages when present.

Analytics & ICS
---------------
* `analytics.py` provides basic anonymous analytics storage (page/view duration,
  anonymized IDs). Enable/disable via `ANALYTICS_ENABLED` and related config
  options.
* The ICS feed (`/calendar.ics`) uses `services_calendar.py` to expand recurring
  series and slots. Include a tokenized URL if you want to restrict access at
  the proxy layer.

Audit Log
---------
* Audit entries are appended as JSON lines to the path defined by
  `GUESTDESK_AUDIT_LOG`. Typical events include `service.create`, `slot.delete`,
  `settings.email.update`, and user CRUD operations.
* Admins can review the tail of this log at `/admin/audit`. The page supports
  selecting how many recent events to display (default 200).
* Ensure the systemd service allows write access to the log file path (see
  `ReadWritePaths` in the service unit below).

Deployment (systemd example)
-----------------------------
```
[Unit]
Description=GuestDesk (Flask) via gunicorn
After=network.target

[Service]
EnvironmentFile=/etc/guestdesk.env
Environment="GUESTDESK_FORCE_SECURE_COOKIES=1"
WorkingDirectory=/opt/guestdesk/guestdesk
ExecStart=/opt/guestdesk/guestdesk/.venv/bin/python -m gunicorn "guestdesk.app:create_app()" \
  --bind 127.0.0.1:8011 --workers 2 --threads 2 \
  --timeout 60 --graceful-timeout 30 --max-requests 2000 --max-requests-jitter 200
User=www-data
Group=www-data
Restart=on-failure
RestartSec=3
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/guestdesk /var/log/guestdesk /var/lib/guestdesk
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
```

For asynchronous jobs, run an RQ worker (example systemd service):
```
[Unit]
Description=GuestDesk RQ Worker
After=redis-server.service
Requires=redis-server.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/guestdesk/guestdesk
EnvironmentFile=/etc/guestdesk.env
ExecStart=/opt/guestdesk/guestdesk/.venv/bin/python -m rq_worker
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Proxy/TLS Notes
----------------
* In production, place a reverse proxy (nginx, Caddy, etc.) in front of gunicorn
  to handle TLS and set `X-Forwarded-Proto`. ProxyFix is already enabled with
  `x_proto=1, x_host=1` to trust those headers.
* When running over plain HTTP, set `GUESTDESK_FORCE_SECURE_COOKIES=0` so CSRF
  and session cookies work correctly; remember to revert to `1` once HTTPS is
  enabled.

Testing
-------
Run pytest inside the virtual environment:

```
source .venv/bin/activate
python -m pytest
```

Only a smoke test is currently included (`tests/test_smoke.py`), so consider
adding behavioural tests around new features or refactors.

Troubleshooting
---------------
* **“attempt to write a readonly database”** – ensure the systemd unit grants
  write access to `/var/lib/guestdesk` (via `ReadWritePaths`) and that the
  directory + SQLite file are owned by `www-data`.
* **CSRF token missing** – verify `SESSION_COOKIE_SECURE` is disabled in HTTP
  environments (set `GUESTDESK_FORCE_SECURE_COOKIES=0`) and clear stale cookies.
* **Email failures** – check SMTP env vars, ensure Redis is reachable if using
  queued delivery, and watch `/var/log/guestdesk/audit.log` or service logs for
  traceback output.
* **Audit log missing** – confirm the file path is writable and that you are
  logged in with an admin role (the dashboard tile hides otherwise).

Contributing
------------
1. Create a feature branch.
2. Install dependencies and run pytest.
3. Submit a PR with a clear summary and testing notes.

Please keep the security posture intact when making changes (CSRF, rate limits,
headers) and update the README when introducing new operational requirements.

License
-------
No explicit license is currently provided. Confirm with the project owners
before reusing in other contexts.
