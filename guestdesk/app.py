# GuestDesk
# Copyright (c) 2025 Chris Tanton
# SPDX-License-Identifier: LicenseRef-GDCL-1.1
from __future__ import annotations
import os
import csv
import math
from datetime import datetime, timedelta, timezone, time as dtime
import secrets
import hashlib
from zoneinfo import ZoneInfo
from pathlib import Path
import io
import json
import shutil
import html as htmlmod
from email.utils import parseaddr
from urllib import request as urlreq, error as urlerr
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from uuid import uuid4
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, g, jsonify, current_app
from dateutil import parser as dtparser
from dateutil.rrule import rrulestr
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from sqlalchemy import create_engine, text, func
from sqlalchemy.orm import sessionmaker, scoped_session
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import RequestEntityTooLarge
from flask_babel import (
    Babel,
    gettext as _,
    lazy_gettext as _l,
    get_locale,
    format_datetime,
    format_date,
    format_time,
    format_currency,
    format_number,
)
from babel.dates import get_day_names
from .models import (
    Base,
    Service,
    Announcement,
    Submission,
    User,
    ServiceSeries,
    ServiceOverride,
    Setting,
    FormPDFConfig,
    GameScore,
    UserContact,
    PasswordResetToken,
    GrievanceCase,
)
from . import pdf_config
from .analytics import init_analytics
from .services_calendar import expand_between
from .mailer import send_category_notification, queue_mail, _recipient_for
from .antispam import seen as idemp_seen, remember as remember_idemp, fetch as fetch_idemp_result
from .audit import log as audit_log
from .permissions import (
    PERMISSION_GROUPS,
    PRESETS,
    permission_required,
    has_permission,
    get_permissions,
    set_permissions,
)
from .grievances import (
    build_grievance_case_id,
    create_case_for_submission,
    render_case_pdf,
    attach_generated_pdf,
)

DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")
# Support both the historical and the clearer env var names
DATA_DIR = (
    os.environ.get("GUESTDESK_DATA_DIR")
    or os.environ.get("GUESTD_DATA_DIR")
    or "/var/lib/guestdesk"
)

csrf = CSRFProtect()
babel = Babel()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
)

def format_time_12(dt_obj: datetime) -> str:
    """Return 12-hour time with AM/PM from a datetime."""
    return dt_obj.strftime('%I:%M %p').lstrip('0')


def looks_like_email(value: str | None) -> bool:
    """Return True for a simple, usable email address value."""
    if not value:
        return False
    _name, addr = parseaddr(value.strip())
    return bool(addr and '@' in addr and '.' in addr.rsplit('@', 1)[-1])


def build_submitter_confirmation_body(
    *,
    kind_label: str,
    reference: str,
    submitted_at: datetime,
    form_values: dict,
    message: str,
    attachment_info: str | None = None,
) -> str:
    """Build a plain-text receipt that includes a copy of the submitted form."""
    rows = [
        _('This confirms that GuestDesk received your %(kind)s.', kind=kind_label),
        _('Reference: %(reference)s', reference=reference),
        _('Submitted: %(timestamp)s', timestamp=submitted_at.strftime('%Y-%m-%d %H:%MZ')),
        '',
        _('Please keep this email for your records.'),
        '',
        _('Copy of your submission:'),
    ]
    for label, value in form_values.items():
        if value:
            rows.append(_('%(label)s: %(value)s', label=label, value=value))
    if attachment_info:
        rows.append(_('Attachments: %(info)s', info=attachment_info))
    rows.extend(['', _('Message:'), message or ''])
    return "\n\n".join([row for row in rows if row is not None])

MAX_GRIEVANCE_DESCRIPTION_LENGTH = 2143
MAINTENANCE_PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif'}
DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
PASSWORD_RESET_TOKEN_TTL = timedelta(hours=1)
MIN_STAFF_PASSWORD_LENGTH = 8


def t(message: str, **kwargs):
    """Shortcut alias for Flask-Babel translations."""
    return _(message, **kwargs)


def human_filesize(num_bytes: int) -> str:
    """Return a short human-readable filesize label."""
    step = 1024.0
    size = float(num_bytes)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < step or unit == 'TB':
            if unit == 'B':
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= step
    return f"{size:.1f} TB"

def create_app():
    """Application factory wiring blueprints, services, and admin UI."""
    app = Flask(__name__)
    env_name = (os.getenv("FLASK_ENV") or os.getenv("ENV") or "").lower()
    is_production = env_name == "production"
    raw_secure_flag = os.getenv(
        "GUESTDESK_FORCE_SECURE_COOKIES",
        "1" if is_production else "0",
    )
    normalized_flag = str(raw_secure_flag).strip().lower()
    use_secure_cookies = normalized_flag in ("1", "true", "yes", "on")
    app.logger.warning(
        "force_secure_cookies=%s (raw=%r, env=%s)",
        use_secure_cookies,
        raw_secure_flag,
        env_name,
    )
    if is_production:
        if not os.getenv("SECRET_KEY") or os.getenv("SECRET_KEY") == "dev-secret":
            raise RuntimeError("SECRET_KEY must be set in the environment for production")
        if not os.getenv("ADMIN_PASSWORD") or os.getenv("ADMIN_PASSWORD") == "changeme":
            raise RuntimeError("ADMIN_PASSWORD must be set in the environment for production")

    app.config.update(
        SESSION_COOKIE_SECURE=use_secure_cookies,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SECURE=use_secure_cookies,
        REMEMBER_COOKIE_HTTPONLY=True,
        WTF_CSRF_SSL_STRICT=use_secure_cookies,
    )

    app.config.setdefault("BABEL_DEFAULT_LOCALE", "en")
    app.config.setdefault("BABEL_DEFAULT_TIMEZONE", "America/New_York")
    app.config.setdefault("BABEL_TRANSLATION_DIRECTORIES", "translations")
    app.config.setdefault("ASSET_VERSION", "4")

    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    Talisman(
        app,
        content_security_policy=None,
        force_https=use_secure_cookies,
        session_cookie_secure=use_secure_cookies,
    )

    def _select_locale() -> str:
        """Select the preferred locale using query param, session, or headers."""
        supported = ("en", "es")
        query_lang = request.args.get("lang")
        if query_lang and query_lang in supported:
            session["lang"] = query_lang
            return query_lang
        sess_lang = session.get("lang")
        if sess_lang in supported:
            return sess_lang
        fallback = request.accept_languages.best_match(supported)
        return fallback or "en"

    csrf.init_app(app)
    limiter.init_app(app)
    babel.init_app(app, locale_selector=_select_locale)

    app.jinja_env.globals.update(
        MAX_GRIEVANCE_DESCRIPTION_LENGTH=MAX_GRIEVANCE_DESCRIPTION_LENGTH,
        grievance_case_id=build_grievance_case_id,
        has_permission=has_permission,
        uuid4=uuid4,
        _=_ ,
        format_datetime=format_datetime,
        format_date=format_date,
        format_time=format_time,
        format_currency=format_currency,
        format_number=format_number,
    )

    @app.before_request
    def _attach_request_id():
        """Attach a request identifier for downstream logging and tracing."""
        g.request_id = request.headers.get("X-Request-ID", str(uuid4()))

    @app.after_request
    def _inject_robots_headers(response):
        """Discourage indexing of any page by default."""
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response

    def _weekday_labels(width: str = 'abbreviated') -> list[str]:
        """Return localized weekday labels in the desired width."""
        locale = str(get_locale() or 'en')
        names = get_day_names(width, locale=locale)
        order = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
        return [names.get(key, names.get(key.upper(), key.title())) for key in order]

    def _parse_upload_limit(raw):
        """Interpret size strings like ``20MB`` into a byte limit."""
        if raw is None:
            return None
        text = str(raw).strip().lower()
        multiplier = 1
        if text.endswith('mb'):
            multiplier = 1024 * 1024
            text = text[:-2].strip()
        elif text.endswith('kb'):
            multiplier = 1024
            text = text[:-2].strip()
        try:
            return int(float(text) * multiplier)
        except Exception:
            return None

    max_upload_bytes = _parse_upload_limit(os.environ.get('GUESTDESK_MAX_UPLOAD_BYTES'))
    if max_upload_bytes is None:
        env_mb = os.environ.get('GUESTDESK_MAX_UPLOAD_MB')
        if env_mb is not None:
            try:
                max_upload_bytes = int(float(str(env_mb).strip())) * 1024 * 1024
            except Exception:
                max_upload_bytes = None
    if max_upload_bytes is None or max_upload_bytes <= 0:
        max_upload_bytes = DEFAULT_MAX_UPLOAD_BYTES
    app.config.setdefault('MAX_CONTENT_LENGTH', max_upload_bytes)
    app.config.setdefault('MAX_UPLOAD_BYTES', max_upload_bytes)
    app.jinja_env.globals['MAX_UPLOAD_BYTES'] = max_upload_bytes
    upload_mb_label = (f"{(max_upload_bytes / (1024 * 1024)):.1f}").rstrip('0').rstrip('.')
    app.jinja_env.globals['MAX_UPLOAD_MB_LABEL'] = upload_mb_label

    @app.errorhandler(RequestEntityTooLarge)
    def handle_large_upload(_err):
        """Redirect back to the form when an upload exceeds the configured cap."""
        limit_bytes = app.config.get('MAX_CONTENT_LENGTH', DEFAULT_MAX_UPLOAD_BYTES)
        limit_mb = limit_bytes / (1024 * 1024)
        # Trim trailing zeros while keeping at most one decimal place
        limit_label = (f"{limit_mb:.1f}").rstrip('0').rstrip('.')
        flash(f'Uploaded file is too large. Limit is {limit_label} MB.', 'danger')
        target = request.referrer or request.path or url_for('report')
        return redirect(target), 303
    # Load env-driven configuration (e.g., mail settings)
    try:
        app.config.from_object("guestdesk.config.Config")
    except Exception:
        # Safe to continue if config module is missing
        pass
    # Grievance template sanity log (helps catch path/env mismatches early)
    # PDF template logging removed (we no longer generate PDFs)
    # Email is handled via guestdesk.mailer using env-driven SMTP; no Flask-Mail init needed.
    # Feature flags (available to templates)
    app.config.setdefault("SHOW_HOME_SERVICES", False)
    # --- Jinja filter: "HH:MM" (24h) -> locale-aware time string
    def h12(t: str) -> str:
        """Render ``HH:MM`` values using the current locale's 12-hour format."""
        if not t:
            return ""
        try:
            parts = (t or "").split(":", 1)
            hours = int(parts[0])
            minutes = int(parts[1][:2]) if len(parts) > 1 else 0
            return format_time(dtime(hour=hours, minute=minutes), format="short")
        except Exception:
            # If the value isn't HH:MM, just show it as-is
            return t

    app.jinja_env.filters["h12"] = h12
    app.config['SECRET_KEY'] = SECRET_KEY
    # Privacy analytics toggles
    app.config.setdefault("ANALYTICS_ENABLED", True)
    app.config.setdefault("ANALYTICS_IP_SALT", os.environ.get("ANALYTICS_IP_SALT", ""))
    os.makedirs(DATA_DIR, exist_ok=True)
    db_path = os.path.join(DATA_DIR, "guestdesk.db")
    engine = create_engine(f"sqlite:///{db_path}", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    try:
        from .grievances import ensure_archive_columns
        ensure_archive_columns(engine)
    except Exception:
        app.logger.exception('Grievance archive column migration failed')
    Session = scoped_session(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    # Initialize analytics blueprint (and ensure table exists)
    try:
        init_analytics(app, engine)
    except Exception:
        # Keep app running even if analytics init fails
        pass

    def dbs():
        """Provide a short-lived database session for request handlers."""
        return Session()
    app.dbs = dbs

    def _hash_reset_token(raw_token: str) -> str:
        """Return a stable hash for storing password reset tokens."""
        return hashlib.sha256((raw_token or '').encode('utf-8')).hexdigest()

    def _coerce_dates_field(val):
        """Normalize rdate/exdate payloads into a comma-delimited string or ``None``."""
        if val is None:
            return None
        if isinstance(val, (list, tuple)):
            cleaned = [str(item).strip() for item in val if str(item).strip()]
            return ",".join(cleaned) if cleaned else None
        s = str(val).strip()
        return s or None

    try:
        from .ics import bp as ics_bp
        app.register_blueprint(ics_bp)
    except Exception as exc:
        app.logger.warning('Failed to register ICS blueprint: %s', exc)

    try:
        from .display import bp as display_bp
        app.register_blueprint(display_bp)
    except Exception as exc:
        app.logger.warning('Failed to register display blueprint: %s', exc)

    try:
        from .grievances import bp as grievances_bp
        app.register_blueprint(grievances_bp)
    except Exception as exc:
        app.logger.warning('Failed to register grievances blueprint: %s', exc)

    @app.teardown_appcontext
    def shutdown_session(_exc=None):
        """Ensure scoped sessions are cleaned up after each request."""
        try:
            Session.remove()
        except Exception:
            pass

    # Load settings from DB into app.config (override defaults)
    try:
        db = dbs()
        for s in db.query(Setting).all():
            key = s.key
            val = s.value or ""
            if key in (
                "GRIEVANCE_EMAIL_TO", "GRIEVANCE_EMAIL_CC",
                "MAINTENANCE_EMAIL_TO", "SUGGESTION_EMAIL_TO", "QUESTION_EMAIL_TO",
            ):
                app.config[key] = [x.strip() for x in val.split(',') if x.strip()]
            else:
                app.config[key] = val
        db.close()
    except Exception:
        pass

    # --- lightweight migration: ensure users.approved and service flags exist ---
    try:
        with engine.connect() as conn:
            cols = [r[1] for r in conn.exec_driver_sql('PRAGMA table_info(users)').all()]
            if 'approved' not in cols:
                conn.exec_driver_sql('ALTER TABLE users ADD COLUMN approved INTEGER NOT NULL DEFAULT 1')
            s_cols = [r[1] for r in conn.exec_driver_sql('PRAGMA table_info(services)').all()]
            if 'availability' not in s_cols:
                conn.exec_driver_sql("ALTER TABLE services ADD COLUMN availability TEXT NOT NULL DEFAULT 'scheduled'")
            if 'is_offsite' not in s_cols:
                conn.exec_driver_sql('ALTER TABLE services ADD COLUMN is_offsite INTEGER NOT NULL DEFAULT 0')
            if 'name_en' not in s_cols:
                conn.exec_driver_sql('ALTER TABLE services ADD COLUMN name_en TEXT')
            if 'name_es' not in s_cols:
                conn.exec_driver_sql('ALTER TABLE services ADD COLUMN name_es TEXT')
            if 'description_en' not in s_cols:
                conn.exec_driver_sql('ALTER TABLE services ADD COLUMN description_en TEXT')
            if 'description_es' not in s_cols:
                conn.exec_driver_sql('ALTER TABLE services ADD COLUMN description_es TEXT')
            if 'location_en' not in s_cols:
                conn.exec_driver_sql('ALTER TABLE services ADD COLUMN location_en TEXT')
            if 'location_es' not in s_cols:
                conn.exec_driver_sql('ALTER TABLE services ADD COLUMN location_es TEXT')
            if 'contact_en' not in s_cols:
                conn.exec_driver_sql('ALTER TABLE services ADD COLUMN contact_en TEXT')
            if 'contact_es' not in s_cols:
                conn.exec_driver_sql('ALTER TABLE services ADD COLUMN contact_es TEXT')
            if 'schedule_note_en' not in s_cols:
                conn.exec_driver_sql('ALTER TABLE services ADD COLUMN schedule_note_en TEXT')
            if 'schedule_note_es' not in s_cols:
                conn.exec_driver_sql('ALTER TABLE services ADD COLUMN schedule_note_es TEXT')
            # backfill defaults when columns newly added
            conn.exec_driver_sql("UPDATE services SET name_en = name WHERE name_en IS NULL")
            conn.exec_driver_sql("UPDATE services SET description_en = description WHERE description_en IS NULL")
            conn.exec_driver_sql("UPDATE services SET location_en = location WHERE location_en IS NULL")
            conn.exec_driver_sql("UPDATE services SET contact_en = contact WHERE contact_en IS NULL")
            conn.exec_driver_sql("UPDATE services SET schedule_note_en = schedule_note WHERE schedule_note_en IS NULL")
            # AnalyticsEvent columns (backfill if missing)
            a_cols = [r[1] for r in conn.exec_driver_sql('PRAGMA table_info(analytics_events)').all()]
            for col, ddl in [
                ('category', 'TEXT'),
                ('action', 'TEXT'),
                ('label', 'TEXT'),
                ('referrer_path', 'TEXT'),
                ('is_staff', 'INTEGER'),
                ('page_load_ms', 'INTEGER'),
                ('anon_id', 'TEXT')
            ]:
                if col not in a_cols:
                    conn.exec_driver_sql(f'ALTER TABLE analytics_events ADD COLUMN {col} {ddl}')
            # Calendar tables (create or backfill columns)
            try:
                conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS service_series (\n\
                    id INTEGER PRIMARY KEY,\n\
                    title TEXT NOT NULL,\n\
                    location TEXT, category TEXT, notes TEXT, tz TEXT,\n\
                    service_id INTEGER,\n\
                    dtstart TEXT NOT NULL, dtend TEXT NOT NULL,\n\
                    rrule TEXT, rdate TEXT, exdate TEXT,\n\
                    is_all_day INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1,\n\
                    created_at TEXT, updated_at TEXT\n\
                )')
            except Exception:
                pass
            ss_cols = [r[1] for r in conn.exec_driver_sql('PRAGMA table_info(service_series)').all()] if conn else []
            if 'service_id' not in ss_cols:
                conn.exec_driver_sql('ALTER TABLE service_series ADD COLUMN service_id INTEGER')
            try:
                conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_service_series_service_id ON service_series(service_id)')
            except Exception:
                pass
            try:
                conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS service_overrides (\n\
                    id INTEGER PRIMARY KEY,\n\
                    series_id INTEGER,\n\
                    service_id INTEGER,\n\
                    instance_start TEXT NOT NULL,\n\
                    new_title TEXT, new_location TEXT, new_dtstart TEXT, new_dtend TEXT,\n\
                    cancelled INTEGER DEFAULT 0\n\
                )')
            except Exception:
                pass
            so_cols = [r[1] for r in conn.exec_driver_sql('PRAGMA table_info(service_overrides)').all()] if conn else []
            if 'service_id' not in so_cols:
                conn.exec_driver_sql('ALTER TABLE service_overrides ADD COLUMN service_id INTEGER')
            try:
                conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_service_overrides_service_id ON service_overrides(service_id)')
            except Exception:
                pass
            # (Removed) legacy PDF templates/bindings tables
            # --- New per-form PDF config
            try:
                conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS form_pdf_config (\n\
                    id INTEGER PRIMARY KEY,\n\
                    form_key TEXT NOT NULL UNIQUE,\n\
                    template_path TEXT,\n\
                    layout_json TEXT,\n\
                    baseline_pad INTEGER NOT NULL DEFAULT 3,\n\
                    attach_to_email INTEGER NOT NULL DEFAULT 0,\n\
                    updated_at TEXT\n\
                )')
            except Exception:
                pass
    except Exception:
        # Best-effort; app can still run without this if table doesn't exist yet
        pass

    # --- user/session helpers (safe no-op if no User model exists) ---
    def load_user():
        """Attach g.user if user_id is in session."""
        g.user = None
        uid = session.get('user_id')
        if uid:
            db = dbs()
            try:
                from .models import User
                g.user = db.get(User, uid)
            except Exception:
                # If User model isn’t present or db lookup fails, ignore.
                g.user = None

    @app.before_request
    def _attach_user():
        """Populate ``g.user`` before each request if the session is authenticated."""
        load_user()

    def _tail_audit_entries(log_path: str, limit: int = 200) -> list[dict[str, object]]:
        """Return the newest JSON audit entries without loading the entire file."""
        try:
            limit_val = int(limit)
        except Exception:
            limit_val = 200
        limit_val = max(1, min(limit_val, 2000))
        path_obj = Path(log_path)
        if not path_obj.exists():
            raise FileNotFoundError(f"Audit log not found at {log_path}")
        data = b""
        chunk = 8192
        with path_obj.open('rb') as fh:
            fh.seek(0, os.SEEK_END)
            file_size = fh.tell()
            pos = file_size
            while pos > 0 and data.count(b"\n") <= limit:
                read_size = min(chunk, pos)
                pos -= read_size
                fh.seek(pos)
                data = fh.read(read_size) + data
        lines = [ln for ln in data.splitlines() if ln.strip()]
        selected = lines[-limit_val:]
        entries: list[dict[str, object]] = []
        for raw_line in selected:
            text = raw_line.decode('utf-8', errors='replace')
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    parsed.setdefault('raw', text)
                    entries.append(parsed)
                    continue
            except Exception:
                pass
            entries.append({'raw': text})
        return entries

    @app.context_processor
    def inject_globals():
        """Expose translation helper and user metadata to every template."""
        return dict(
            t=t,
            lang=str(get_locale() or 'en'),
            user_name=session.get('username'),
            user_role=session.get('role'),
            grievance_case_id=build_grievance_case_id,
        )

    @app.context_processor
    def inject_audit_flag():
        """Let templates know if the audit log view is registered."""
        available = False
        try:
            available = 'admin_audit' in current_app.view_functions
        except Exception:
            pass
        return dict(audit_log_available=available)

    @app.context_processor
    def inject_csrf_token():
        """Provide a callable for generating CSRF tokens inside templates."""
        return dict(csrf_token=lambda: generate_csrf())

    @app.context_processor
    def inject_flags():
        """Expose feature flags like ``SHOW_HOME_SERVICES`` to templates."""
        return dict(SHOW_HOME_SERVICES=app.config["SHOW_HOME_SERVICES"])

    @app.context_processor
    def inject_asset_version():
        """Expose ``ASSET_VERSION`` for cache-busting static assets."""
        return dict(ASSET_VERSION=app.config.get("ASSET_VERSION", "4"))

    @app.get('/_healthz')
    def _healthz():
        """Lightweight readiness probe consumed by uptime monitors."""
        return {"ok": True, "version": os.getenv("GUESTDESK_VERSION", "dev")}, 200

    @app.route('/robots.txt')
    def robots_txt():
        """Serve a restrictive robots.txt discouraging indexing."""
        body = "User-agent: *\nDisallow: /\n"
        return current_app.response_class(body, mimetype="text/plain")

    @app.route('/lang/<code>')
    def set_lang(code):
        """Persist the visitor's language preference in the session."""
        session['lang'] = code if code in ('en', 'es') else 'en'
        next_url = request.args.get('next')
        return redirect(next_url or request.referrer or url_for('home'))

    @app.route('/')
    def home():
        """Render the guest-facing dashboard with announcements and service counts."""
        db = dbs()
        now = datetime.utcnow()
        anns = db.query(Announcement).filter(
            Announcement.starts_at <= now,
        ).filter(
            (Announcement.ends_at.is_(None)) | (Announcement.ends_at >= now)
        ).order_by(Announcement.starts_at.desc()).limit(5).all()
        cats = ['Food','Showers','Laundry','Mail','ID/Docs','Medical','Mental Health','Legal','Employment','Transportation','Other']
        counts = {c: db.query(Service).filter(Service.category==c).count() for c in cats}
        return render_template('home.html', anns=anns, counts=counts, cats=cats)

    @app.route('/services')
    def services():
        """List all services, optionally filtered by category."""
        db = dbs()
        cat = request.args.get('cat')
        q = db.query(Service)
        if cat:
            q = q.filter(Service.category == cat)
        rows = q.order_by(Service.category, func.lower(func.coalesce(Service.name_en, Service.name))).all()
        return render_template('services.html', rows=rows, cat=cat)

    @app.route('/service/<int:sid>')
    def service_detail(sid:int):
        """Show a single service with its weekly schedule."""
        db = dbs()
        s = db.get(Service, sid)
        if not s:
            abort(404)
        from dateutil.parser import isoparse
        from .services_calendar import merged_occurrences

        tzname = current_app.config.get('BABEL_DEFAULT_TIMEZONE', 'America/New_York')
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            tz = timezone.utc

        now = datetime.now(tz)
        window_end = now + timedelta(days=21)
        events = merged_occurrences(db, now, window_end, service_id=s.id, tzname=tzname)

        occurrences = []
        for ev in events:
            try:
                sdt = isoparse(ev.get('start')).astimezone(tz)
                edt = isoparse(ev.get('end')).astimezone(tz)
            except Exception:
                continue
            occurrences.append({
                'start': sdt,
                'end': edt,
                'title': ev.get('title') or s.name,
                'location': ev.get('location') or s.location,
            })

        occurrences.sort(key=lambda item: item['start'])
        occurrences = occurrences[:12]

        return render_template(
            'service_detail.html',
            s=s,
            occurrences=occurrences,
        )

    @app.route('/schedule')
    def schedule():
        """Render either the classic matrix view or the dynamic calendar view."""
        db = dbs()
        days = _weekday_labels('abbreviated')

        from dateutil.parser import isoparse
        from .services_calendar import merged_occurrences

        tzname = current_app.config.get('BABEL_DEFAULT_TIMEZONE', 'America/New_York')
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            tz = timezone.utc
            tzname = 'UTC'

        now = datetime.now(tz)
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        week_end = week_start + timedelta(days=7)

        events = merged_occurrences(db, week_start, week_end, tzname=tzname)

        bucketed = {i: [] for i in range(7)}
        for ev in events:
            try:
                sdt = isoparse(ev.get('start')).astimezone(tz)
                edt = isoparse(ev.get('end')).astimezone(tz)
            except Exception:
                continue
            bucketed[sdt.weekday()].append(
                {
                    'title': ev.get('title') or '',
                    'location': ev.get('location') or '',
                    'start_hhmm': f"{sdt.hour:02d}:{sdt.minute:02d}",
                    'end_hhmm': f"{edt.hour:02d}:{edt.minute:02d}",
                }
            )

        for i in range(7):
            bucketed[i].sort(key=lambda x: x['start_hhmm'])

        return render_template('schedule_dynamic.html', days=days, bucketed=bucketed)

    @app.route('/announcements')
    def announcements():
        """Display active announcements for guests."""
        db = dbs()
        now = datetime.utcnow()
        anns = db.query(Announcement).filter(Announcement.starts_at <= now).filter(
            (Announcement.ends_at.is_(None)) | (Announcement.ends_at >= now)
        ).order_by(Announcement.starts_at.desc()).all()
        return render_template('announcements.html', anns=anns)

    # ----- Submissions (guest) -----
    @app.route('/report')
    def report():
        """Landing page for selecting a submission form."""
        return render_template('report.html')

    @app.route('/submit/<kind>', methods=['GET','POST'])
    @limiter.limit("10/minute")
    def submit(kind):
        """Handle guest submissions for maintenance, grievances, suggestions, or questions."""
        if kind not in ['maintenance','grievance','suggestion','question']:
            abort(404)
        if request.method == 'POST':
            photo_plan = None
            photo_file = request.files.get('photo') if kind == 'maintenance' else None
            if photo_file and photo_file.filename:
                filename = secure_filename(photo_file.filename)
                ext = Path(filename or '').suffix.lower()
                if ext not in MAINTENANCE_PHOTO_EXTENSIONS:
                    flash(_('Please upload a JPG, PNG, or GIF image.'), 'danger')
                    return render_template('submit_kind.html', kind=kind, form=request.form)
                safe_name = filename or f'maintenance-photo{ext}'
                timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
                planned_name = f"{timestamp}_{safe_name}"
                try:
                    photo_bytes = photo_file.read()
                except Exception:
                    flash(_('Could not read the uploaded photo. Please try again.'), 'danger')
                    return render_template('submit_kind.html', kind=kind, form=request.form)
                if not photo_bytes:
                    flash(_('The uploaded photo appears to be empty. Please choose a different file.'), 'danger')
                    return render_template('submit_kind.html', kind=kind, form=request.form)
                photo_file.stream.seek(0)
                photo_plan = {
                    'file': photo_file,
                    'name': planned_name,
                    'bytes': photo_bytes,
                    'mimetype': photo_file.mimetype or 'application/octet-stream',
                    'path': None,
                    'display_path': None,
                }
            # Accept 'description' as the required field for grievances
            body = ((request.form.get('description') or '') if kind == 'grievance' else (request.form.get('body') or '')).strip()
            if kind == 'grievance' and len(body) > MAX_GRIEVANCE_DESCRIPTION_LENGTH:
                flash(f'Description is limited to {MAX_GRIEVANCE_DESCRIPTION_LENGTH:,} characters.', 'danger')
                return render_template('submit_kind.html', kind=kind, form=request.form)
            if not body:
                flash(_('Please add some details.'), 'danger')
                return render_template('submit_kind.html', kind=kind, form=request.form)
            subject_val = (request.form.get('subject') or '').strip() or None
            category_val = (request.form.get('category') or '').strip() or None
            building_val = (request.form.get('building') or '').strip() or None
            location_val = (request.form.get('location') or '').strip() or None
            if kind == 'grievance':
                contact_name_val = (request.form.get('name') or request.form.get('contact_name') or '').strip() or None
                contact_bits = [
                    (request.form.get('phone') or request.form.get('contact_info') or '').strip(),
                    (request.form.get('email') or '').strip(),
                ]
                contact_info_val = ', '.join([bit for bit in contact_bits if bit]) or None
            else:
                contact_name_val = (request.form.get('contact_name') or '').strip() or None
                contact_info_val = (request.form.get('contact_info') or '').strip() or None
            token = (request.form.get('idempotency_key') or '').strip() or None
            body_lower = body.lower()

            db = dbs()

            already_seen = False
            try:
                already_seen = idemp_seen(token)
            except Exception:
                already_seen = False
            if already_seen:
                existing = None
                existing_id = fetch_idemp_result(token)
                if existing_id:
                    existing = db.get(Submission, existing_id)
                if existing is None:
                    existing = (
                        db.query(Submission)
                        .filter(Submission.kind == kind)
                        .filter(func.lower(Submission.body) == body_lower)
                        .order_by(Submission.created_at.desc())
                        .first()
                    )
                if existing:
                    flash(_('Looks like this submission was already received. We kept the earlier one.'), 'info')
                    return render_template('thanks.html', sub=existing)
                flash(_('Looks like this submission was already received. Thanks for your patience!'), 'info')
                return render_template('submit_kind.html', kind=kind, form=request.form)

            if kind == 'maintenance':
                dedupe_window = datetime.utcnow() - timedelta(minutes=1)
                duplicate_q = (
                    db.query(Submission)
                    .filter(Submission.kind == 'maintenance')
                    .filter(Submission.created_at >= dedupe_window)
                    .filter(func.lower(Submission.body) == body_lower)
                )
                for column, value in [
                    (Submission.subject, subject_val),
                    (Submission.category, category_val),
                    (Submission.building, building_val),
                    (Submission.location, location_val),
                    (Submission.contact_name, contact_name_val),
                    (Submission.contact_info, contact_info_val),
                ]:
                    if value is None:
                        duplicate_q = duplicate_q.filter(column.is_(None))
                    else:
                        duplicate_q = duplicate_q.filter(column == value)
                existing = duplicate_q.order_by(Submission.created_at.desc()).first()
                if existing:
                    app.logger.info('Deduplicated maintenance submission, returning existing submission #%s', existing.id)
                    flash(_('Looks like this maintenance request was already received a moment ago. We will use the earlier one.'), 'info')
                    return render_template('thanks.html', sub=existing)

            sub = Submission(
                kind=kind,
                subject=subject_val,
                body=body,
                category=category_val,
                building=building_val,
                location=location_val,
                contact_name=contact_name_val,
                contact_info=contact_info_val,
            )
            db.add(sub)
            try:
                db.flush()
            except Exception:
                db.rollback()
                raise
            if photo_plan:
                upload_root = Path(DATA_DIR) / 'uploads' / kind / str(sub.id)
                try:
                    upload_root.mkdir(parents=True, exist_ok=True)
                    photo_path = upload_root / photo_plan['name']
                    photo_plan['file'].save(photo_path)
                    photo_plan['path'] = str(photo_path)
                    try:
                        photo_plan['display_path'] = str(photo_path.relative_to(DATA_DIR))
                    except ValueError:
                        photo_plan['display_path'] = photo_plan['path']
                except Exception as exc:
                    db.rollback()
                    app.logger.exception('Failed to save maintenance photo: %s', exc)
                    flash(_('Could not save the uploaded photo. Please try again.'), 'danger')
                    return render_template('submit_kind.html', kind=kind, form=request.form)
            db.commit()
            # Create the tracker case in its own transaction so a tracker fault
            # can never break guest intake; the backfill script covers any gap.
            grievance_case = None
            if kind == 'grievance':
                try:
                    grievance_case = create_case_for_submission(db, sub, source='guest_digital', form=request.form)
                    db.commit()
                except Exception:
                    db.rollback()
                    grievance_case = None
                    app.logger.exception('Failed to create grievance case for submission #%s', sub.id)
            remote_addr = request.headers.get('X-Forwarded-For', request.remote_addr)
            if kind == 'grievance':
                stored_case_id = build_grievance_case_id(sub.id, sub.created_at)
                app.logger.info(
                    'Grievance submission stored: id=%s case_id=%s has_email=%s remote_addr=%s',
                    sub.id,
                    stored_case_id,
                    bool((request.form.get('email') or '').strip()),
                    remote_addr,
                )
                audit_log(
                    'grievance.submission.stored',
                    actor='guest',
                    obj=stored_case_id,
                    extra={
                        'submission_id': sub.id,
                        'has_email': bool((request.form.get('email') or '').strip()),
                        'remote_addr': remote_addr,
                    },
                )
            else:
                app.logger.info('Submission stored: kind=%s id=%s remote_addr=%s', kind, sub.id, remote_addr)
            if token:
                try:
                    remember_idemp(token, sub.id)
                except Exception:
                    pass
            grievance_case_id = build_grievance_case_id(sub.id, sub.created_at) if kind == 'grievance' else None
            # Build normalized payload for renderer
            payload = {
                'name': (request.form.get('contact_name') or request.form.get('name') or '').strip() or None,
                'email': (request.form.get('email') or '').strip() or None,
                'phone': (request.form.get('phone') or request.form.get('contact_info') or '').strip() or None,
                'subject': (request.form.get('subject') or '').strip() or None,
                'description': (request.form.get('description') or request.form.get('body') or request.form.get('message') or '').strip() or None,
                'category': (request.form.get('category') or '').strip() or None,
                'building': (request.form.get('building') or '').strip() or None,
                'location': (request.form.get('location') or '').strip() or None,
            }
            # Attempt per-form PDF render (FormPDFConfig)
            attachments = []
            attach_info = None
            if photo_plan:
                attachments.append((photo_plan['mimetype'], photo_plan['name'], photo_plan['bytes']))
                attach_info = f"Photo: {photo_plan['display_path'] or photo_plan['path']}"
            try:
                cfg = db.query(FormPDFConfig).filter(FormPDFConfig.form_key == kind).first()
                if kind == 'grievance' and grievance_case is not None:
                    # Case-based render: same template/layout, plus the intake
                    # header stamp; stored on the case even when email is off.
                    pdf_bytes = render_case_pdf(db, grievance_case, sub)
                    if pdf_bytes:
                        attach_generated_pdf(db, grievance_case, pdf_bytes, actor_label='system')
                        db.commit()
                        # Archival copy in the pre-tracker output location
                        out_dir = os.path.join(pdf_config.output_root(), kind, str(sub.id))
                        os.makedirs(out_dir, exist_ok=True)
                        out_path = os.path.join(out_dir, f"{kind}-{sub.id}.pdf")
                        with open(out_path, 'wb') as fh:
                            fh.write(pdf_bytes)
                        if cfg and cfg.attach_to_email:
                            fname = f"{kind}-{sub.id}.pdf"
                            attachments.append(("application/pdf", fname, pdf_bytes))
                            note = f"Form: {kind} • File: {out_path}"
                            attach_info = f"{attach_info} • {note}" if attach_info else note
                elif cfg and cfg.attach_to_email and cfg.template_path and cfg.layout_json:
                    import os
                    from .pdf_render import render_pdf
                    # Map submission to renderer payload
                    def bool_to_checkbox(b):
                        """Return ``True`` when a checkbox should render as checked."""
                        return True if b else False
                    def pdf_payload_for_form(form_key, submission):
                        """Normalize submission data into the PDF renderer schema."""
                        k = (form_key or '').strip().lower()
                        if k == 'grievance':
                            import datetime as _dt
                            # Normalize values from grievance form names
                            name_val = (request.form.get('name') or request.form.get('contact_name') or '').strip()
                            phone_val = (request.form.get('phone') or request.form.get('contact_info') or '').strip()
                            email_val = (request.form.get('email') or '').strip()
                            staff_name = (request.form.get('staff_involved') or request.form.get('name_of_staff_involved') or request.form.get('staff_involved_name') or '').strip()
                            involves_staff = bool_to_checkbox(request.form.get('involves_grace_staff') or request.form.get('involves_staff'))
                            involves_policies = bool_to_checkbox(request.form.get('involves_policies'))
                            involves_volunteer = bool_to_checkbox(request.form.get('involves_volunteer'))
                            other_txt = (request.form.get('involves_other') or request.form.get('involves_other_txt') or '').strip()
                            involves_other = bool_to_checkbox(request.form.get('involves_other_chk') or other_txt)
                            desc_val = (request.form.get('description') or submission.body or '')
                            case_id_val = grievance_case_id or build_grievance_case_id(submission.id, submission.created_at)
                            data_map = {
                                'id': case_id_val,
                                'case_id': case_id_val,
                                'submission_id': submission.id,
                                'todays_date': _dt.datetime.utcnow().strftime('%Y-%m-%d'),
                                'submitted_date': submission.created_at.strftime('%Y-%m-%d'),
                                'submitted_time': format_time_12(submission.created_at),
                                'staff_involved': staff_name,
                                'name': name_val or (submission.contact_name or ''),
                                'phone': phone_val or (submission.contact_info or ''),
                                'email': email_val,
                                'involves_staff': involves_staff,
                                'involves_grace_staff': involves_staff,
                                'involves_policies': involves_policies,
                                'involves_volunteer': involves_volunteer,
                                'involves_other': involves_other,
                                'involves_other_txt': other_txt,
                                'other': other_txt,
                                'contact_name': name_val or (submission.contact_name or ''),
                                'description': desc_val,
                            }
                            return data_map
                        if k == 'maintenance':
                            import datetime as _dt
                            return {
                                'id': submission.id,
                                'todays_date': _dt.datetime.utcnow().strftime('%Y-%m-%d'),
                                'submitted_date': submission.created_at.strftime('%Y-%m-%d'),
                                'submitted_time': format_time_12(submission.created_at),
                                'name': (request.form.get('contact_name') or '').strip() or (submission.contact_name or ''),
                                'phone': (request.form.get('phone') or request.form.get('contact_info') or '').strip() or (submission.contact_info or ''),
                                'email': (request.form.get('email') or '').strip(),
                                'category': (request.form.get('category') or submission.category or ''),
                                'location': (request.form.get('location') or submission.location or ''),
                                'description': (request.form.get('description') or submission.body or ''),
                            }
                        # default mapping
                        import datetime as _dt
                        return {
                            'id': submission.id,
                            'todays_date': _dt.datetime.utcnow().strftime('%Y-%m-%d'),
                            'submitted_date': submission.created_at.strftime('%Y-%m-%d'),
                            'submitted_time': format_time_12(submission.created_at),
                            'name': (request.form.get('contact_name') or '').strip() or (submission.contact_name or ''),
                            'phone': (request.form.get('phone') or request.form.get('contact_info') or '').strip() or (submission.contact_info or ''),
                            'email': (request.form.get('email') or '').strip(),
                            'subject': submission.subject or '',
                            'description': (request.form.get('description') or submission.body or ''),
                        }
                    data = pdf_payload_for_form(kind, sub)
                    pdf_bytes = render_pdf(cfg.template_path, cfg.layout_json, data, pad=float(cfg.baseline_pad or 3), debug=False)
                    # write to file
                    out_dir = os.path.join(pdf_config.output_root(), kind, str(sub.id))
                    os.makedirs(out_dir, exist_ok=True)
                    out_path = os.path.join(out_dir, f"{kind}-{sub.id}.pdf")
                    with open(out_path, 'wb') as fh:
                        fh.write(pdf_bytes)
                    fname = f"{kind}-{sub.id}.pdf"
                    attachments.append(("application/pdf", fname, pdf_bytes))
                    note = f"Form: {kind} • File: {out_path}"
                    attach_info = f"{attach_info} • {note}" if attach_info else note
            except Exception as e:
                app.logger.exception('PDF render failed: %s', e)
            notification_status = {
                'staff_notice_queued': False,
                'confirmation_notice_queued': False,
                'confirmation_email': None,
                'notification_error': False,
            }
            # Send category-specific notification (non-blocking on failure)
            try:
                msg_text = (
                    (request.form.get('message') or '').strip()
                    or (request.form.get('description') or '').strip()
                    or (request.form.get('body') or '').strip()
                )
                extra_bits = []
                if request.form.get('category'):
                    extra_bits.append(f"Category: {request.form.get('category')}")
                if request.form.get('building'):
                    extra_bits.append(f"Building: {request.form.get('building')}")
                if request.form.get('location'):
                    extra_bits.append(f"Location: {request.form.get('location')}")
                if request.form.get('contact_info'):
                    extra_bits.append(f"Contact: {request.form.get('contact_info')}")
                extra = "; ".join(extra_bits) if extra_bits else None

                kind_labels = {
                    'maintenance': _('Maintenance Issue'),
                    'grievance': _('Grievance'),
                    'suggestion': _('Suggestion / Idea'),
                    'question': _('Question'),
                }
                default_subjects = {
                    'maintenance': _('[GuestDesk] Maintenance Issue'),
                    'grievance': _('[GuestDesk] Grievance'),
                    'suggestion': _('[GuestDesk] Suggestion / Idea'),
                    'question': _('[GuestDesk] Question'),
                }
                subject = (request.form.get('subject') or '').strip() or default_subjects.get(kind, _('[GuestDesk] Submission'))
                category_label = kind_labels.get(kind, kind.title())
                submitter_email = (request.form.get('email') or '').strip()
                confirmation_to = submitter_email if looks_like_email(submitter_email) else None
                if confirmation_to:
                    notification_status['confirmation_email'] = confirmation_to
                staff_sender = None

                if kind == 'grievance':
                    import datetime as _dt
                    now = _dt.datetime.utcnow()
                    grv_id = grievance_case_id or build_grievance_case_id(sub.id, sub.created_at)
                    to_list = [e.strip() for e in current_app.config.get('GRIEVANCE_EMAIL_TO', []) if e and e.strip()]
                    cc_list = [e.strip() for e in current_app.config.get('GRIEVANCE_EMAIL_CC', []) if e and e.strip()]
                    sender = current_app.config.get('GRIEVANCE_FROM')
                    staff_sender = sender
                    contact_bits = [
                        (request.form.get('phone') or request.form.get('contact_info') or '').strip(),
                        submitter_email,
                    ]
                    contact_value = ", ".join([c for c in contact_bits if c])
                    body_lines = [
                        _("A new grievance has been submitted."),
                        _("ID: %(case)s", case=grv_id),
                        _("Submitted: %(timestamp)s", timestamp=now.strftime('%Y-%m-%d %H:%MZ')),
                        _("From: %(name)s (%(contact)s)",
                          name=(request.form.get('name') or request.form.get('contact_name') or '').strip(),
                          contact=contact_value or _('not provided')),
                        "",
                        _("Message:"),
                        msg_text or "",
                    ]
                    if attach_info:
                        body_lines.append(_("Attachments: %(info)s", info=attach_info))
                    queue_mail(
                        subject=_('[GuestDesk] Grievance %(case)s', case=grv_id),
                        body="\n\n".join([line for line in body_lines if line is not None]),
                        to=to_list or [current_app.config.get('GRIEVANCE_EMAIL') or current_app.config.get('ADMIN_EMAIL')],
                        cc=cc_list,
                        sender=sender,
                        attachments=attachments,
                        reply_to=submitter_email if confirmation_to else None,
                    )
                    notification_status['staff_notice_queued'] = True
                    staff_recipients = to_list or [current_app.config.get('GRIEVANCE_EMAIL') or current_app.config.get('ADMIN_EMAIL')]
                    app.logger.info(
                        'Grievance staff notification queued: id=%s case_id=%s to_count=%s cc_count=%s',
                        sub.id,
                        grv_id,
                        len([addr for addr in staff_recipients if addr]),
                        len(cc_list),
                    )
                    audit_log(
                        'grievance.notification.staff_queued',
                        actor='system',
                        obj=grv_id,
                        extra={
                            'submission_id': sub.id,
                            'to_count': len([addr for addr in staff_recipients if addr]),
                            'cc_count': len(cc_list),
                        },
                    )
                else:
                    # Default behavior for other categories
                    # Augment body with attach_info if present
                    lines = {
                        'name': (request.form.get('contact_name') or '').strip() or None,
                        'email': (request.form.get('email') or '').strip() or None,
                        'phone': (request.form.get('phone') or '').strip() or None,
                        'subject': subject,
                        'message': msg_text,
                        'url': request.url,
                        'extra': (extra + (('\n' + attach_info) if attach_info else '')) if extra else attach_info,
                    }
                    body_parts = [
                        _("Category: %(category)s", category=category_label),
                        _("Subject: %(value)s", value=lines['subject']) if lines['subject'] else None,
                        _("Name: %(value)s", value=lines['name']) if lines['name'] else None,
                        _("Email: %(value)s", value=lines['email']) if lines['email'] else None,
                        _("Phone: %(value)s", value=lines['phone']) if lines['phone'] else None,
                        _("Page URL: %(value)s", value=lines['url']) if lines['url'] else None,
                        _("Extra: %(value)s", value=lines['extra']) if lines['extra'] else None,
                        "",
                        _("Message:"),
                        str(lines['message'] or ''),
                    ]
                    body_text = "\n\n".join([part for part in body_parts if part is not None])
                    to_list = _recipient_for(kind)
                    queue_mail(
                        subject=subject,
                        body=body_text,
                        to=to_list,
                        reply_to=submitter_email if confirmation_to else None,
                        attachments=attachments or None,
                    )
                    notification_status['staff_notice_queued'] = True

                if confirmation_to:
                    reference = grievance_case_id or f"#{sub.id}"
                    form_values = {
                        _('Name'): (request.form.get('name') or request.form.get('contact_name') or '').strip(),
                        _('Email'): confirmation_to,
                        _('Phone'): (request.form.get('phone') or request.form.get('contact_info') or '').strip(),
                        _('Subject'): (request.form.get('subject') or '').strip(),
                        _('Category'): (request.form.get('category') or '').strip(),
                        _('Building'): (request.form.get('building') or '').strip(),
                        _('Location'): (request.form.get('location') or '').strip(),
                        _('Staff Involved'): (request.form.get('staff_involved') or '').strip(),
                        _('Incident Date'): (request.form.get('incident_date') or '').strip(),
                        _('Incident Time'): (request.form.get('incident_time') or '').strip(),
                        _('Other'): (request.form.get('involves_other') or '').strip(),
                    }
                    confirmation_body = build_submitter_confirmation_body(
                        kind_label=category_label,
                        reference=reference,
                        submitted_at=sub.created_at,
                        form_values=form_values,
                        message=msg_text or body,
                        attachment_info=attach_info,
                    )
                    queue_mail(
                        subject=_('[GuestDesk] %(kind)s Confirmation %(reference)s', kind=category_label, reference=reference),
                        body=confirmation_body,
                        to=[confirmation_to],
                        sender=staff_sender,
                        attachments=attachments or None,
                    )
                    notification_status['confirmation_notice_queued'] = True
                    if kind == 'grievance':
                        app.logger.info(
                            'Grievance submitter confirmation queued: id=%s case_id=%s submitter_email=%s',
                            sub.id,
                            reference,
                            confirmation_to,
                        )
                        audit_log(
                            'grievance.notification.confirmation_queued',
                            actor='system',
                            obj=reference,
                            extra={
                                'submission_id': sub.id,
                                'has_submitter_email': True,
                            },
                        )
                elif kind == 'grievance':
                    skipped_case_id = grievance_case_id or build_grievance_case_id(sub.id, sub.created_at)
                    app.logger.info(
                        'Grievance submitter confirmation skipped: id=%s case_id=%s reason=no_valid_email',
                        sub.id,
                        skipped_case_id,
                    )
                    audit_log(
                        'grievance.notification.confirmation_skipped',
                        actor='system',
                        obj=skipped_case_id,
                        extra={
                            'submission_id': sub.id,
                            'reason': 'no_valid_email',
                        },
                    )
            except Exception as e:
                notification_status['notification_error'] = True
                app.logger.exception('Failed to queue/send %s email notification for submission id=%s: %s', kind, getattr(sub, 'id', None), e)
            return render_template('thanks.html', sub=sub, case_id=grievance_case_id, notification_status=notification_status)
        return render_template('submit_kind.html', kind=kind, form={})

    # Development-only mail smoke test
    @app.get('/_mail_test')
    def _mail_test():
        """Send a test maintenance notification to verify SMTP wiring."""
        try:
            send_category_notification("maintenance", {
                "name": "Smoke Test",
                "email": "no-reply@gracemarketplace.org",
                "message": "This is a smoke test from /_mail_test.",
                "url": request.url,
            })
            return "ok", 200
        except Exception as e:
            app.logger.exception("Smoke test failed: %s", e)
            return f"error: {e}", 500

    # (Removed) PDF grievance health endpoint

    # ----- Fun zone -----
    OFFLINE_JOKES = [
        "Why did the server go to therapy? Too many unresolved requests.",
        "I told the electrician a joke. He was shocked.",
        "I tried to catch some fog. I mist.",
        "Parallel lines have so much in common. It’s a shame they’ll never meet."
    ]
    OFFLINE_QUOTES = [
        "One day at a time.",
        "You matter. A lot.",
        "Small steps still move you forward.",
        "The best time to start was yesterday. The next best time is now."
    ]
    OFFLINE_TRIVIA = [
        ("How many bones are in the adult human body?", "206"),
        ("What’s the capital of Florida?", "Tallahassee"),
        ("Which planet is known as the Red Planet?", "Mars"),
    ]

    # Simple in-memory cache for Fun Zone content
    FUN_CACHE_TTL = timedelta(minutes=5)
    fun_cache = {
        "at": None,
        "live": False,
        "joke": None, "joke_live": False,
        "quote": None, "quote_live": False,
        "trivia_q": None, "trivia_a": None, "trivia_live": False,
    }

    @app.route('/fun')
    def fun():
        """Serve light entertainment (joke, quote, trivia) for the guest portal."""
        import random
        # Serve cached live pieces; retry fetching for anything not live or stale
        now = datetime.utcnow()
        fresh = fun_cache["at"] and (now - fun_cache["at"]) < FUN_CACHE_TTL
        joke = fun_cache["joke"] if (fresh and fun_cache.get("joke_live")) else None
        quote = fun_cache["quote"] if (fresh and fun_cache.get("quote_live")) else None
        trivia_q = fun_cache["trivia_q"] if (fresh and fun_cache.get("trivia_live")) else None
        trivia_a = fun_cache["trivia_a"] if (fresh and fun_cache.get("trivia_live")) else None
        # Try live sources first with short timeouts; fall back to offline lists.
        # Keep track of what we fetch live during this request
        joke_live = False
        quote_live = False
        trivia_live = False

        # Track which items came from live sources
        # Joke: Official Joke API
        if joke is None:
            try:
                with urlreq.urlopen('https://official-joke-api.appspot.com/random_joke', timeout=1.5) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    setup = (data.get('setup') or '').strip()
                    punch = (data.get('punchline') or '').strip()
                    if setup or punch:
                        joke = f"{setup} {'— ' if setup and punch else ''}{punch}".strip()
                        joke_live = True
            except Exception:
                pass

        # Quote: Quotable API
        if quote is None:
            try:
                with urlreq.urlopen('https://api.quotable.io/random', timeout=1.5) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    content = (data.get('content') or '').strip()
                    author = (data.get('author') or '').strip()
                    if content:
                        quote = f"{content}"
                        if author:
                            quote += f" — {author}"
                        quote_live = True
            except Exception:
                pass

        # Trivia: Open Trivia DB
        if trivia_q is None or trivia_a is None:
            try:
                with urlreq.urlopen('https://opentdb.com/api.php?amount=1&type=multiple', timeout=1.5) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    results = data.get('results') or []
                    if results:
                        q = htmlmod.unescape(results[0].get('question') or '')
                        a = htmlmod.unescape(results[0].get('correct_answer') or '')
                        if q and a:
                            trivia_q, trivia_a = q, a
                            trivia_live = True
            except Exception:
                pass

        # Fallbacks
        if not joke:
            joke = random.choice(OFFLINE_JOKES)
        if not quote:
            quote = random.choice(OFFLINE_QUOTES)
        if not (trivia_q and trivia_a):
            trivia_q, trivia_a = random.choice(OFFLINE_TRIVIA)

        # Update cache; only mark fresh if any live content fetched
        any_live = joke_live or quote_live or trivia_live
        fun_cache.update({
            "at": (now if any_live else fun_cache.get("at")),
            "live": any_live or fun_cache.get("live", False),
            "joke": joke, "joke_live": joke_live or fun_cache.get("joke_live", False),
            "quote": quote, "quote_live": quote_live or fun_cache.get("quote_live", False),
            "trivia_q": trivia_q, "trivia_a": trivia_a, "trivia_live": trivia_live or fun_cache.get("trivia_live", False),
        })

        return render_template('fun.html', joke=joke, quote=quote, trivia_q=trivia_q, trivia_a=trivia_a)

    @app.route('/funzone')
    def funzone():
        """Render the Fun Zone page and inject the optional mobile controls."""
        # Render base funzone then enhance with mobile controls script
        html = render_template('funzone.html')
        try:
            js_url = url_for('static', filename='vendor/funzone_mobile.js')
            if '</body>' in html:
                html = html.replace('</body>', f'<script src="{js_url}"></script></body>')
        except Exception:
            pass
        return html

    # ----- Arcade leaderboards (Snake/Tetris) -----
    @app.route('/arcade/scores/<game>', methods=['GET'])
    def arcade_scores(game: str):
        """Return the top scores for a given arcade game as JSON."""
        game = (game or '').strip().lower()
        limit = max(1, min(50, int(request.args.get('limit', 10))))
        db = dbs()
        try:
            from .models import GameScore
        except Exception:
            return jsonify({"scores": []})
        rows = (
            db.query(GameScore)
            .filter(GameScore.game == game)
            .order_by(GameScore.score.desc(), GameScore.created_at.asc())
            .limit(limit)
            .all()
        )
        return jsonify({
            "scores": [
                {
                    "id": r.id,
                    "name": r.name,
                    "score": r.score,
                    "at": r.created_at.isoformat()
                } for r in rows
            ]
        })

    @app.route('/arcade/scores/<game>', methods=['POST'])
    def arcade_submit_score(game: str):
        """Persist a submitted arcade score and respond with the player's rank."""
        game = (game or '').strip().lower()
        if not game:
            return jsonify({"ok": False, "error": "invalid game"}), 400
        data = request.get_json(silent=True) or request.form
        name = (data.get('name') or 'Anonymous').strip()
        if len(name) > 40:
            name = name[:40]
        try:
            score = int(data.get('score') or 0)
        except Exception:
            score = 0
        if score <= 0:
            return jsonify({"ok": False, "error": "invalid score"}), 400
        try:
            from .models import GameScore
        except Exception:
            return jsonify({"ok": False, "error": "model missing"}), 500
        db = dbs()
        row = GameScore(game=game, name=name or 'Anonymous', score=score)
        db.add(row)
        db.commit()
        # compute rank (1-based)
        try:
            greater = db.execute(
                "SELECT COUNT(1) FROM game_scores WHERE game = :g AND score > :s",
                {"g": game, "s": score}
            ).scalar() or 0
            rank = int(greater) + 1
        except Exception:
            rank = None
        return jsonify({"ok": True, "id": row.id, "rank": rank, "score": row.score, "name": row.name})

    # ----- Staff auth & admin -----
    def current_user():
        """Retrieve the current ``User`` record when authenticated."""
        uid = session.get('user_id')
        if not uid:
            return None
        db = dbs()
        return db.get(User, uid)

    def login_required(fn):
        """Decorator that redirects unauthenticated visitors to the login screen."""
        @wraps(fn)
        def _wrap(*a, **kw):
            """Redirect to login when the request lacks an authenticated user."""
            # Let admin session OR a logged-in user through
            if session.get("is_admin") or session.get("admin"):
                return fn(*a, **kw)
            u = getattr(g, "user", None)
            if u:
                return fn(*a, **kw)
            return redirect(url_for("login", next=request.path))
        return _wrap
    def roles_required(*required_roles):
        """Decorator enforcing that the current user carries one of the roles."""
        def deco(fn):
            """Wrap a view to enforce that the user has an allowed role."""
            @wraps(fn)
            def _wrap(*a, **kw):
                """Abort with 403 when the visitor lacks the appropriate role."""
                # one-password admin (no DB user) bypasses role checks
                if session.get("is_admin") or session.get("admin"):
                    return fn(*a, **kw)
                # real user must have one of the required roles
                u = getattr(g, "user", None)
                if u and ((getattr(u, "role", "") or "").lower() in [r.lower() for r in required_roles]):
                    return fn(*a, **kw)
                return abort(403)
            return _wrap
        return deco

    def audit_actor() -> str:
        """Identify the string actor for audit logging (user, admin session, anon)."""
        user = getattr(g, 'user', None)
        if user and getattr(user, 'username', None):
            return str(user.username)
        if session.get('is_admin') or session.get('admin'):
            return 'admin-session'
        if session.get('username'):
            return str(session['username'])
        return 'anonymous'
    # Ensure there is at least one admin user
    db = dbs()
    if not db.query(User).filter(User.role == 'admin').first():
        if not db.query(User).first():
            admin = User(
                username='admin',
                role='admin',
                password_hash=generate_password_hash(DEFAULT_ADMIN_PASSWORD),
                approved=True,
            )
            db.add(admin)
            db.commit()

    @app.route('/signup', methods=['GET', 'POST'])
    def signup():
        """Allow prospective staff to request a viewer account pending approval."""
        db = dbs()
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip()
            password = request.form.get('password') or ''
            if not username or not password:
                flash(_('Username and password required.'), 'danger')
                return render_template('signup.html', form=request.form)
            if db.query(User).filter(User.username == username).first():
                flash(_('Username already exists.'), 'danger')
                return render_template('signup.html', form=request.form)
            u = User(
                username=username,
                role='viewer',
                password_hash=generate_password_hash(password),
                approved=False,
            )
            db.add(u)
            db.commit()
            flash(_('Account created. Awaiting staff approval before login.'), 'success')
            return redirect(url_for('home'))
        return render_template('signup.html', form={})

    @app.route('/forgot-password', methods=['GET', 'POST'])
    @limiter.limit("5/hour")
    def forgot_password():
        """Allow staff to request a password reset link via email."""
        if request.method == 'POST':
            email = (request.form.get('email') or '').strip()
            if not email:
                flash(_('Please enter the email address associated with your account.'), 'warning')
                return render_template('forgot_password.html', email=email)

            normalized = email.lower()
            db = dbs()
            contact = (
                db.query(UserContact)
                .filter(func.lower(UserContact.email) == normalized)
                .first()
            )

            if contact and contact.user and contact.user.approved:
                try:
                    raw_token = secrets.token_urlsafe(32)
                    now = datetime.utcnow()
                    token = PasswordResetToken(
                        user_id=contact.user_id,
                        token_hash=_hash_reset_token(raw_token),
                        requested_at=now,
                        expires_at=now + PASSWORD_RESET_TOKEN_TTL,
                        request_ip=(request.remote_addr or '')[:64],
                        user_agent=request.headers.get('User-Agent'),
                    )
                    db.add(token)
                    db.commit()

                    reset_url = url_for('reset_password', token=raw_token, _external=True)
                    subject = _('Reset your GuestDesk password')
                    body = _(
                        'Hello %(name)s,\n\n'
                        'We received a request to reset your GuestDesk password. '
                        'Use the link below within the next hour to choose a new password.\n\n'
                        '%(url)s\n\n'
                        'If you did not request this, you can ignore this email.',
                        name=contact.user.username,
                        url=reset_url,
                    )
                    queue_mail(subject=subject, body=body, to=contact.email)
                except Exception as exc:
                    app.logger.exception('Failed to issue password reset: %s', exc)
                    db.rollback()

            flash(_('If we find a matching account, a reset link will be emailed shortly.'), 'info')
            return redirect(url_for('login'))

        return render_template('forgot_password.html')

    @app.route('/reset-password/<token>', methods=['GET', 'POST'])
    @limiter.limit("10/hour")
    def reset_password(token):
        """Validate a password reset token and let staff choose a new password."""
        if not token:
            flash(_('That password reset link is invalid or has expired.'), 'danger')
            return redirect(url_for('login'))

        db = dbs()
        token_hash = _hash_reset_token(token)
        now = datetime.utcnow()
        record = (
            db.query(PasswordResetToken)
            .filter(PasswordResetToken.token_hash == token_hash)
            .filter(PasswordResetToken.used_at.is_(None))
            .first()
        )

        if not record or not record.user or record.expires_at < now:
            flash(_('That password reset link is invalid or has expired.'), 'danger')
            return redirect(url_for('login'))

        if request.method == 'POST':
            password = request.form.get('password') or ''
            confirm = request.form.get('confirm_password') or ''

            if len(password) < MIN_STAFF_PASSWORD_LENGTH:
                flash(_('Password must be at least %(length)d characters long.', length=MIN_STAFF_PASSWORD_LENGTH), 'danger')
                return render_template('reset_password.html')
            if password != confirm:
                flash(_('Passwords do not match.'), 'danger')
                return render_template('reset_password.html')

            try:
                record.user.password_hash = generate_password_hash(password)
                record.used_at = now
                db.commit()
                flash(_('Your password has been updated. You can log in now.'), 'success')
                return redirect(url_for('login'))
            except Exception as exc:
                db.rollback()
                app.logger.exception('Failed to apply password reset: %s', exc)
                flash(_('We could not update your password. Please try again.'), 'danger')

        return render_template('reset_password.html')

    @app.route('/login', methods=['GET', 'POST'])
    @limiter.limit("5/minute")
    def login():
        """Authenticate a staff account and persist details in the session."""
        # Carry next from query or form so POST preserves it; default to home
        next_url = request.args.get('next') or request.form.get('next') or url_for('home')
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip()
            password = request.form.get('password') or ''
            db = dbs()
            u = db.query(User).filter(User.username == username).first()
            if u and not getattr(u, 'approved', True):
                flash(_('Account pending approval. Please contact an administrator.'), 'warning')
                return render_template('login.html')
            if u and check_password_hash(u.password_hash, password):
                session['user_id'] = u.id
                session['username'] = u.username
                session['role'] = u.role
                flash(_('Welcome back.'), 'success')
                return redirect(next_url)
            flash(_('Wrong username or password.'), 'danger')
        return render_template('login.html')

    @app.route('/account/security', methods=['GET', 'POST'])
    @login_required
    def account_security():
        """Let authenticated staff manage their recovery email address."""
        user = getattr(g, 'user', None)
        if not user:
            return redirect(url_for('login', next=request.path))

        db = dbs()
        contact = db.query(UserContact).filter(UserContact.user_id == user.id).first()
        email = contact.email if contact else ''

        if request.method == 'POST':
            new_email = (request.form.get('email') or '').strip()
            if not new_email:
                flash(_('Please enter a valid email address.'), 'danger')
                return render_template('account_profile.html', email=new_email)

            normalized = new_email.lower()
            existing = (
                db.query(UserContact)
                .filter(func.lower(UserContact.email) == normalized)
                .filter(UserContact.user_id != user.id)
                .first()
            )
            if existing:
                flash(_('That email is already in use by another account.'), 'danger')
                return render_template('account_profile.html', email=new_email)

            try:
                if not contact:
                    contact = UserContact(user_id=user.id, email=new_email)
                    db.add(contact)
                else:
                    contact.email = new_email
                db.commit()
                flash(_('Account settings updated.'), 'success')
                return redirect(url_for('account_security'))
            except Exception as exc:
                db.rollback()
                app.logger.exception('Failed to update account settings: %s', exc)
                flash(_('We could not update your account right now. Please try again.'), 'danger')
                return render_template('account_profile.html', email=new_email)

        return render_template('account_profile.html', email=email)

    @app.route('/logout')
    def logout():
        """Clear the session and bounce visitors back to the guest homepage."""
        session.clear()
        return redirect(url_for('home'))

    # --- Admin landing ---
    @app.route('/admin')
    @login_required
    def admin_index():
        """Landing page for administrators with recent submission highlights."""
        # Admin/editor dashboard
        svc_count = 0
        ann_count = 0
        try:
            db = dbs()
            svc_count = db.query(Service).count()
            ann_count = db.query(Announcement).count()
        except Exception as e:
            # Log and continue with defaults
            app.logger.exception("Admin dashboard load failed: %s", e)
        return render_template('admin/index.html', svc_count=svc_count, ann_count=ann_count)

    # --- manage services ---
    @app.route('/admin/analytics')
    @roles_required('admin')
    def admin_analytics():
        """Render the analytics dashboard shell (data fetched via JSON)."""
        # Render dashboard shell; data loads via JSON APIs below
        return render_template('admin/analytics.html')

    # --- Email settings (admin) ---
    @app.route('/admin/email-settings', methods=['GET', 'POST'])
    @permission_required('settings.grievance_email.edit')
    def admin_email_settings():
        """Manage category-specific notification email settings."""
        db = dbs()
        keys = [
            'MAINTENANCE_EMAIL_TO',
            'GRIEVANCE_EMAIL_TO', 'GRIEVANCE_EMAIL_CC', 'GRIEVANCE_FROM',
            'SUGGESTION_EMAIL_TO', 'QUESTION_EMAIL_TO',
        ]
        if request.method == 'POST':
            before_data = {k: app.config.get(k) for k in keys}
            for k in keys:
                raw = (request.form.get(k) or '').strip()
                # Persist as plain string; lists are CSV
                s = db.get(Setting, k)
                if not s:
                    s = Setting(key=k, value=raw)
                    db.add(s)
                else:
                    s.value = raw
                # Also update live app config
                if k in ('GRIEVANCE_EMAIL_TO', 'GRIEVANCE_EMAIL_CC', 'MAINTENANCE_EMAIL_TO', 'SUGGESTION_EMAIL_TO', 'QUESTION_EMAIL_TO'):
                    app.config[k] = [x.strip() for x in raw.split(',') if x.strip()]
                else:
                    app.config[k] = raw
            db.commit()
            after_data = {k: app.config.get(k) for k in keys}
            audit_log(
                "settings.email.update",
                actor=audit_actor(),
                before=before_data,
                after=after_data,
            )
            flash(_('Email settings updated.'), 'success')
            return redirect(url_for('admin_email_settings'))
        # Compose current values (lists joined by commas)
        vals = {}
        for k in keys:
            v = app.config.get(k)
            if isinstance(v, (list, tuple)):
                vals[k] = ','.join(v)
            else:
                vals[k] = v or ''
        return render_template('admin/email_settings.html', vals=vals)

    # ---- Analytics JSON APIs ----
    def _analytics_range():
        """Interpret date filters from the query string and return UTC bounds."""
        tzname = app.config.get("ANALYTICS_TZ", "America/New_York")
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            tz = ZoneInfo("UTC")
        q_from = request.args.get('from')
        q_to = request.args.get('to')
        today_local = datetime.now(tz).date()
        if q_from and q_to:
            try:
                start_date = datetime.fromisoformat(q_from).date()
                end_date = datetime.fromisoformat(q_to).date()
            except ValueError:
                start_date = today_local - timedelta(days=29)
                end_date = today_local
        else:
            end_date = today_local
            start_date = end_date - timedelta(days=29)
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        start_local = datetime.combine(start_date, datetime.min.time(), tzinfo=tz)
        end_local = datetime.combine(end_date, datetime.min.time(), tzinfo=tz) + timedelta(days=1)
        start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
        end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
        return start_date, end_date, start_utc, end_utc

    def _staff_filter_sql() -> str:
        """Translate the staff filter into a SQL WHERE clause fragment."""
        staff = (request.args.get('staff') or '').strip()
        if staff == '1':
            return " AND COALESCE(is_staff,0)=1"
        if staff == '0':
            return " AND COALESCE(is_staff,0)=0"
        return ""

    def _bind_list(prefix: str, values: list[str]) -> tuple[str, dict[str, str]]:
        """Return placeholders and params for binding a list into SQL."""
        bits = []
        params: dict[str, str] = {}
        for idx, val in enumerate(values):
            key = f"{prefix}_{idx}"
            bits.append(f":{key}")
            params[key] = val
        return ", ".join(bits), params

    def _compute_p95(samples: list[int]) -> int | None:
        """Compute the 95th percentile for a set of integer samples."""
        if not samples:
            return None
        samples.sort()
        idx = max(0, math.ceil(0.95 * len(samples)) - 1)
        return samples[idx]

    def _load_samples(conn, base_params: dict[str, object], staff_clause: str, paths: list[str]) -> dict[str, list[int]]:
        """Fetch latency samples grouped by path from analytics_events."""
        placeholders, extra = _bind_list('path', paths)
        if not placeholders:
            return {}
        sql = f"""
            SELECT path,
                   {load_expr} AS load
            FROM analytics_events
            WHERE started_at >= :start AND started_at < :end
              {staff_clause}
              AND path IN ({placeholders})
        """
        rows = conn.execute(text(sql), {**base_params, **extra}).all()
        out: dict[str, list[int]] = {}
        for path, load in rows:
            try:
                value = int(load)
            except Exception:
                continue
            if value <= 0:
                continue
            out.setdefault(path, []).append(value)
        return out

    def _maybe_csv(filename: str, headers: list[str], rows: list[tuple]):
        """Emit a CSV attachment when ``?format=csv`` is supplied."""
        if (request.args.get('format') or '').lower() != 'csv':
            return None
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
        resp = current_app.response_class(buf.getvalue(), mimetype='text/csv')
        resp.headers['Content-Disposition'] = f'attachment; filename={filename}'
        return resp

    unique_expr = "COALESCE(NULLIF(ip_hash,''), NULLIF(anon_id,''), NULLIF(client_id,''), NULLIF(session_id,''))"
    load_expr = "CASE WHEN page_load_ms IS NULL OR page_load_ms <= 0 THEN duration_ms ELSE page_load_ms END"

    @app.get('/admin/analytics/api/summary')
    @roles_required('admin')
    def analytics_api_summary():
        """Return aggregate visit counts and submission totals for the window."""
        _, _, start_dt, end_dt = _analytics_range()
        params = dict(start=start_dt, end=end_dt)
        staff_clause = _staff_filter_sql()
        sql = f"""
            SELECT
                COUNT(*) AS total,
                COUNT(DISTINCT {unique_expr}) AS uniques,
                SUM(CASE WHEN category = 'form' THEN 1 ELSE 0 END) AS forms,
                SUM(CASE WHEN COALESCE(is_staff,0)=1 THEN 1 ELSE 0 END) AS staff
            FROM analytics_events
            WHERE started_at >= :start AND started_at < :end
            {staff_clause}
        """
        with engine.connect() as conn:
            res = conn.execute(text(sql), params).mappings().first()
        total = int((res or {}).get('total') or 0)
        staff_hits = int((res or {}).get('staff') or 0)
        guests = max(0, total - staff_hits)
        return jsonify(dict(
            total=total,
            uniques=int((res or {}).get('uniques') or 0),
            form_submissions=int((res or {}).get('forms') or 0),
            staff=staff_hits,
            guests=guests,
        ))

    @app.get('/admin/analytics/api/timeseries')
    @roles_required('admin')
    def analytics_api_timeseries():
        """Provide daily hits/unique counts for charting."""
        start_date, end_date, start_dt, end_dt = _analytics_range()
        params = dict(start=start_dt, end=end_dt)
        staff_clause = _staff_filter_sql()
        sql = f"""
            SELECT DATE(started_at) AS day,
                   COUNT(*) AS hits,
                   COUNT(DISTINCT {unique_expr}) AS uniques
            FROM analytics_events
            WHERE started_at >= :start AND started_at < :end
            {staff_clause}
            GROUP BY day
            ORDER BY day
        """
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        data = [{"date": str(r['day']), "hits": int(r['hits']), "uniques": int(r['uniques'])} for r in rows]
        csv_rows = [(d["date"], d["hits"], d["uniques"]) for d in data]
        csv_resp = _maybe_csv('analytics-timeseries.csv', ['date', 'hits', 'uniques'], csv_rows)
        if csv_resp:
            return csv_resp
        return jsonify(data)

    @app.get('/admin/analytics/api/top-pages')
    @roles_required('admin')
    def analytics_api_top_pages():
        """Return top paths with average and p95 load times."""
        _, _, start_dt, end_dt = _analytics_range()
        params = dict(start=start_dt, end=end_dt)
        staff_clause = _staff_filter_sql()
        sql = f"""
            SELECT path,
                   COUNT(*) AS views,
                   AVG({load_expr}) AS avg_ms
            FROM analytics_events
            WHERE started_at >= :start AND started_at < :end
            {staff_clause}
            GROUP BY path
            ORDER BY views DESC
            LIMIT 25
        """
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
            paths = [r['path'] for r in rows]
            samples = _load_samples(conn, params, staff_clause, paths)
        data = []
        for r in rows:
            avg_val = r['avg_ms']
            p95_val = _compute_p95(samples.get(r['path'], []))
            data.append({
                "path": r['path'],
                "views": int(r['views']),
                "avg_ms": int(avg_val or 0),
                "p95_ms": int(p95_val or 0),
            })
        csv_rows = [(d['path'], d['views'], d['avg_ms'], d['p95_ms']) for d in data]
        csv_resp = _maybe_csv('analytics-top-pages.csv', ['path', 'views', 'avg_ms', 'p95_ms'], csv_rows)
        if csv_resp:
            return csv_resp
        return jsonify(data)

    @app.get('/admin/analytics/api/flows')
    @roles_required('admin')
    def analytics_api_flows():
        """Summarize most common navigation transitions."""
        _, _, start_dt, end_dt = _analytics_range()
        params = dict(start=start_dt, end=end_dt)
        staff_clause = _staff_filter_sql()
        sql = f"""
            SELECT prev_path, path, COUNT(*) AS transitions
            FROM (
                SELECT path,
                       LAG(path) OVER (PARTITION BY session_id ORDER BY started_at) AS prev_path
                FROM analytics_events
                WHERE started_at >= :start AND started_at < :end
                {staff_clause}
            ) AS seq
            WHERE prev_path IS NOT NULL
            GROUP BY prev_path, path
            ORDER BY transitions DESC
            LIMIT 50
        """
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        data = [{"from": r['prev_path'] or '(direct)', "to": r['path'], "count": int(r['transitions'])} for r in rows]
        return jsonify(data)

    @app.get('/admin/analytics/api/categories')
    @roles_required('admin')
    def analytics_api_categories():
        """Count events grouped by analytics category attribute."""
        _, _, start_dt, end_dt = _analytics_range()
        params = dict(start=start_dt, end=end_dt)
        staff_clause = _staff_filter_sql()
        sql = f"""
            SELECT COALESCE(NULLIF(category,''), 'uncategorized') AS cat,
                   COUNT(*) AS c
            FROM analytics_events
            WHERE started_at >= :start AND started_at < :end
            {staff_clause}
            GROUP BY cat
            ORDER BY c DESC
        """
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        data = [{"category": r['cat'], "count": int(r['c'])} for r in rows]
        return jsonify(data)

    @app.get('/admin/analytics/api/forms')
    @roles_required('admin')
    def analytics_api_forms():
        """Return top form labels within the selected window."""
        _, _, start_dt, end_dt = _analytics_range()
        params = dict(start=start_dt, end=end_dt)
        staff_clause = _staff_filter_sql()
        sql = f"""
            SELECT COALESCE(NULLIF(label,''), 'unknown') AS form,
                   COUNT(*) AS c
            FROM analytics_events
            WHERE started_at >= :start AND started_at < :end
              {staff_clause}
              AND category = 'form'
            GROUP BY form
            ORDER BY c DESC
            LIMIT 50
        """
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        data = [{"form": r['form'], "count": int(r['c'])} for r in rows]
        return jsonify(data)

    @app.get('/admin/analytics/api/perf')
    @roles_required('admin')
    def analytics_api_perf():
        """Surface paths with the slowest observed load times."""
        _, _, start_dt, end_dt = _analytics_range()
        params = dict(start=start_dt, end=end_dt)
        staff_clause = _staff_filter_sql()
        sql = f"""
            SELECT path,
                   COUNT(*) AS samples,
                   AVG({load_expr}) AS avg_ms
            FROM analytics_events
            WHERE started_at >= :start AND started_at < :end
              {staff_clause}
            GROUP BY path
        """
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
            paths = [r['path'] for r in rows]
            samples = _load_samples(conn, params, staff_clause, paths)
        stats = []
        for r in rows:
            path = r['path']
            if not samples.get(path):
                continue
            avg_val = r['avg_ms']
            p95_val = _compute_p95(samples[path])
            stats.append({
                "path": path,
                "avg_ms": int(avg_val or 0),
                "p95_ms": int(p95_val or 0),
                "samples": int(r['samples']),
            })
        stats.sort(key=lambda x: x['p95_ms'], reverse=True)
        stats = stats[:25]
        csv_rows = [(d['path'], d['samples'], d['avg_ms'], d['p95_ms']) for d in stats]
        csv_resp = _maybe_csv('analytics-performance.csv', ['path', 'samples', 'avg_ms', 'p95_ms'], csv_rows)
        if csv_resp:
            return csv_resp
        return jsonify(stats)

    # PDF calibrator removed

    @app.route('/admin/services')
    @permission_required('services.view')
    def admin_services():
        """List services for editing within the admin console."""
        db = dbs()
        rows = db.query(Service).order_by(Service.category, Service.name).all()
        return render_template('admin/services.html', rows=rows)

    @app.route('/admin/audit')
    @roles_required('admin')
    def admin_audit():
        """Tail the structured audit log inside the admin UI."""
        log_path = os.getenv('GUESTDESK_AUDIT_LOG', '/var/log/guestdesk/audit.log')
        limit_param = request.args.get('n', '200')
        try:
            limit_val = int(limit_param)
        except Exception:
            limit_val = 200
        error = None
        entries: list[dict[str, object]] = []
        try:
            entries = _tail_audit_entries(log_path, limit_val)
        except Exception as exc:
            error = str(exc)
        return render_template(
            'admin/audit.html',
            entries=list(reversed(entries)),
            log_path=log_path,
            limit=limit_val,
            error=error,
        )

    # ---- Services Calendar ----
    @app.get('/admin/services/options')
    @permission_required('services.view')
    def admin_services_options():
        """Return lightweight id/name pairs for service selectors."""
        db = dbs()
        try:
            rows = (
                db.query(Service.id, func.coalesce(Service.name_en, Service.name).label('name'))
                .order_by(func.lower(func.coalesce(Service.name_en, Service.name)))
                .all()
            )
            return jsonify([{"id": r.id, "name": r.name} for r in rows])
        finally:
            db.close()

    @app.route('/admin/services/calendar')
    @permission_required('services.view')
    def admin_services_calendar():
        """Render the calendar management view spanning all services."""
        return render_template('admin/services_calendar.html', sid=None)

    @app.route('/admin/services/<int:sid>/calendar')
    @permission_required('services.view')
    def admin_services_calendar_one(sid:int):
        """Render the calendar management view scoped to a single service."""
        return render_template('admin/services_calendar.html', sid=sid)

    @app.get('/admin/services/feed')
    @permission_required('services.view')
    def admin_services_feed():
        """Return merged service occurrences for FullCalendar in the admin UI."""
        from dateutil.parser import isoparse
        try:
            s = request.args.get('start') or ''
            e = request.args.get('end') or ''
            start = isoparse(s)
            end = isoparse(e)
        except Exception:
            return jsonify([])
        svc_id = request.args.get('service_id', type=int)
        db = dbs()
        try:
            from .services_calendar import merged_occurrences
            events = merged_occurrences(db, start, end, service_id=svc_id)
            return jsonify(events)
        finally:
            db.close()

    @app.get('/admin/services/preview')
    @permission_required('services.view')
    def admin_services_preview():
        """Return the next N occurrence datetimes for a prospective schedule."""
        rrule_str = (request.args.get('rrule') or '').strip()
        dtstart_raw = (request.args.get('dtstart') or '').strip()
        if not dtstart_raw:
            return jsonify({'ok': False, 'error': 'missing_dtstart'}), 400

        tzname = (request.args.get('tz') or 'America/New_York').strip()
        count = request.args.get('n', type=int) or 6

        try:
            tz = ZoneInfo(tzname)
        except Exception:
            tz = ZoneInfo('America/New_York')

        try:
            dtstart = dtparser.parse(dtstart_raw)
        except Exception as exc:
            return jsonify({'ok': False, 'error': f'bad_dtstart: {exc}'}), 400

        if dtstart.tzinfo is None:
            dtstart = dtstart.replace(tzinfo=tz)

        dates: list[datetime] = []
        if not rrule_str:
            dates = [dtstart]
        else:
            try:
                rule = rrulestr(rrule_str, dtstart=dtstart)
            except Exception as exc:
                return jsonify({'ok': False, 'error': f'bad_rrule: {exc}'}), 400

            cursor = dtstart
            for _ in range(max(1, count)):
                nxt = rule.after(cursor, inc=True)
                if not nxt:
                    break
                dates.append(nxt)
                cursor = nxt

        return jsonify({'ok': True, 'dates': [d.isoformat() for d in dates]})

    @app.get('/admin/services/series')
    @permission_required('services.view')
    def admin_series_list():
        """List series optionally filtered by service_id for table display."""
        svc_id = request.args.get('service_id', type=int)
        db = dbs()
        try:
            q = db.query(ServiceSeries)
            if svc_id:
                q = q.filter(ServiceSeries.service_id == svc_id)
            rows = q.order_by(ServiceSeries.id.desc()).all()
            return jsonify([
                {
                    "id": row.id,
                    "service_id": row.service_id,
                    "service_name": (row.service.name_en if row.service else None) or (row.service.name if row.service else None),
                    "title": row.title,
                    "rrule": row.rrule,
                    "rdate": row.rdate,
                    "exdate": row.exdate,
                    "dtstart": row.dtstart.isoformat() if row.dtstart else None,
                    "dtend": row.dtend.isoformat() if row.dtend else None,
                }
                for row in rows
            ])
        finally:
            db.close()

    @app.get('/admin/services/series/<int:series_id>')
    @permission_required('services.view')
    def admin_series_detail(series_id: int):
        """Return a single series for editing."""
        db = dbs()
        try:
            series = db.get(ServiceSeries, series_id)
            if not series:
                abort(404)
            return jsonify({
                "id": series.id,
                "service_id": series.service_id,
                "service_name": (series.service.name_en if series.service else None) or (series.service.name if series.service else None),
                "title": series.title,
                "rrule": series.rrule,
                "rdate": series.rdate,
                "exdate": series.exdate,
                "dtstart": series.dtstart.isoformat() if series.dtstart else None,
                "dtend": series.dtend.isoformat() if series.dtend else None,
            })
        finally:
            db.close()

    @app.post('/admin/services/series')
    @permission_required('services.edit')
    def admin_series_create():
        """Create a new recurring service series definition."""
        data = request.get_json(force=True) or {}
        db = dbs()
        try:
            dtstart = fromiso(data.get('dtstart'))
            dtend = fromiso(data.get('dtend'))
            if not dtstart or not dtend:
                return jsonify({'ok': False, 'error': 'invalid_datetime'}), 400

            title = (data.get('title') or '').strip() or 'Untitled Service'
            tzval = data.get('timezone') or data.get('tz') or 'America/New_York'
            rrule = (data.get('rrule') or '').strip() or None
            rdate = _coerce_dates_field(data.get('rdate'))
            exdate = _coerce_dates_field(data.get('exdate'))
            is_all_day = bool(data.get('is_all_day', False))

            s = ServiceSeries(
                title=title,
                location=data.get('location'),
                category=data.get('category'),
                notes=data.get('notes'),
                tz=tzval,
                dtstart=dtstart,
                dtend=dtend,
                rrule=rrule,
                rdate=rdate,
                exdate=exdate,
                is_all_day=is_all_day,
                is_active=True,
            )

            svc = None
            if data.get('service_id'):
                try:
                    s.service_id = int(data.get('service_id'))
                    svc = db.get(Service, s.service_id)
                except Exception:
                    svc = None
            if svc:
                fallback_name = (svc.name_en or svc.name or '').strip()
                if not s.title or s.title.lower() == 'untitled service':
                    s.title = fallback_name or s.title
                if not s.location:
                    s.location = svc.location_en or svc.location
                if not s.category:
                    s.category = svc.category

            db.add(s)
            db.commit()
            return jsonify({'ok': True, 'id': s.id}), 201
        finally:
            db.close()

    @app.put('/admin/services/series/<int:series_id>')
    @app.patch('/admin/services/series/<int:series_id>')
    @roles_required('admin', 'editor')
    def admin_series_update(series_id:int):
        """Update an existing series with changes from the calendar editor."""
        data = request.get_json(force=True) or {}
        db = dbs()
        try:
            s = db.get(ServiceSeries, series_id)
            if not s:
                abort(404)

            if 'title' in data:
                title_val = (data.get('title') or '').strip()
                if title_val:
                    s.title = title_val
            if 'location' in data:
                s.location = data.get('location')
            if 'category' in data:
                s.category = data.get('category')
            if 'notes' in data:
                s.notes = data.get('notes')
            if 'timezone' in data or 'tz' in data:
                s.tz = data.get('timezone') or data.get('tz') or s.tz
            if 'rrule' in data:
                s.rrule = (data.get('rrule') or '').strip() or None
            if 'is_all_day' in data:
                s.is_all_day = bool(data.get('is_all_day'))
            if 'is_active' in data:
                s.is_active = bool(data.get('is_active'))
            if 'dtstart' in data:
                new_start = fromiso(data.get('dtstart'))
                if new_start:
                    s.dtstart = new_start
            if 'dtend' in data:
                new_end = fromiso(data.get('dtend'))
                if new_end:
                    s.dtend = new_end
            if 'rdate' in data:
                s.rdate = _coerce_dates_field(data.get('rdate'))
            if 'exdate' in data:
                s.exdate = _coerce_dates_field(data.get('exdate'))
            if data.get('service_id'):
                try:
                    s.service_id = int(data.get('service_id'))
                except Exception:
                    pass

            if s.service_id:
                svc = db.get(Service, s.service_id)
                if svc:
                    fallback_name = (svc.name_en or svc.name or '').strip()
                    if not s.title or s.title.lower() == 'untitled service':
                        s.title = fallback_name or s.title
                    if not s.location:
                        s.location = svc.location_en or svc.location
                    if not s.category:
                        s.category = svc.category

            db.commit()
            return jsonify({'ok': True})
        finally:
            db.close()

    @app.delete('/admin/services/series/<int:series_id>')
    @roles_required('admin', 'editor')
    def admin_series_delete(series_id: int):
        """Delete a recurring series definition."""
        db = dbs()
        try:
            series = db.get(ServiceSeries, series_id)
            if not series:
                abort(404)
            db.delete(series)
            db.commit()
            return jsonify({'ok': True})
        finally:
            db.close()

    @app.post('/admin/services/override')
    @permission_required('services.edit')
    def admin_series_override():
        """Persist one-off overrides or cancellations for a service instance."""
        data = request.get_json(force=True)
        db = dbs()
        try:
            ov = ServiceOverride(
                series_id = (int(data['series_id']) if data.get('series_id') else None),
                service_id = (int(data['service_id']) if data.get('service_id') else None),
                instance_start = fromiso(data.get('instance_start')),
                new_title = data.get('new_title'),
                new_location = data.get('new_location'),
                new_dtstart = (fromiso(data['new_dtstart']) if data.get('new_dtstart') else None),
                new_dtend = (fromiso(data['new_dtend']) if data.get('new_dtend') else None),
                cancelled = bool(data.get('cancelled', False)),
            )
            db.add(ov)
            db.commit()
            return jsonify({'id': ov.id}), 201
        finally:
            db.close()

    def fromiso(s:str|None):
        """Parse ISO8601 strings while allowing ``None`` as passthrough."""
        from dateutil.parser import isoparse as _isoparse
        if not s:
            return None
        return _isoparse(s)

    @app.route('/admin/services/new', methods=['GET', 'POST'])
    @permission_required('services.edit')
    def admin_services_new():
        """Create a new service entry from the admin form."""
        if request.method == 'POST':
            db = dbs()
            def _clean(value: str | None) -> str:
                """Strip whitespace and coerce ``None`` to empty string."""
                return (value or '').strip()
            name_en = _clean(request.form.get('name_en')) or 'Unnamed'
            name_es = _clean(request.form.get('name_es'))
            desc_en = _clean(request.form.get('description_en'))
            desc_es = _clean(request.form.get('description_es'))
            location_en = _clean(request.form.get('location_en'))
            location_es = _clean(request.form.get('location_es'))
            contact_en = _clean(request.form.get('contact_en'))
            contact_es = _clean(request.form.get('contact_es'))
            note_en = _clean(request.form.get('schedule_note_en'))
            note_es = _clean(request.form.get('schedule_note_es'))
            s = Service(
                name=name_en,
                name_en=name_en,
                name_es=name_es or None,
                category=request.form.get('category') or 'Other',
                availability=(request.form.get('availability') or 'scheduled'),
                is_offsite=bool(request.form.get('is_offsite')),
                description=desc_en or '',
                description_en=desc_en or None,
                description_es=desc_es or None,
                location=location_en,
                location_en=location_en or None,
                location_es=location_es or None,
                contact=contact_en,
                contact_en=contact_en or None,
                contact_es=contact_es or None,
                schedule_note=note_en,
                schedule_note_en=note_en or None,
                schedule_note_es=note_es or None,
                external_link=_clean(request.form.get('external_link')) or '',
            )
            db.add(s)
            db.commit()
            audit_log(
                "service.create",
                actor=audit_actor(),
                obj=s.id,
                after={
                    "name": s.name,
                    "category": s.category,
                    "availability": s.availability,
                },
            )
            flash(_('Service created.'), 'success')
            next_url = request.form.get('next') or request.args.get('next')
            if next_url:
                parsed = urlparse(next_url)
                if not parsed.scheme and not parsed.netloc:
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    query['service_id'] = [str(s.id)]
                    new_query = urlencode(query, doseq=True)
                    dest = urlunparse(parsed._replace(query=new_query))
                    return redirect(dest)
            return redirect(url_for('admin_services'))
        next_url = request.args.get('next', '')
        return render_template('admin/services_new.html', next_url=next_url)

    @app.route('/admin/services/<int:sid>/edit', methods=['GET', 'POST'])
    @permission_required('services.edit')
    def admin_services_edit(sid: int):
        """Edit an existing service including localized fields and metadata."""
        db = dbs()
        s = db.get(Service, sid)
        if not s:
            abort(404)
        if request.method == 'POST':
            def _clean(value: str | None) -> str:
                """Normalize optional form values to trimmed strings."""
                return (value or '').strip()
            before_data = {
                "name": s.name,
                "category": s.category,
                "availability": s.availability,
                "is_offsite": s.is_offsite,
            }
            name_en = _clean(request.form.get('name_en')) or s.name_en or s.name
            name_es = _clean(request.form.get('name_es'))
            s.name = name_en or s.name
            s.name_en = name_en or None
            s.name_es = name_es or None
            s.category = request.form.get('category') or s.category
            s.availability = request.form.get('availability') or s.availability
            s.is_offsite = bool(request.form.get('is_offsite'))
            desc_en = _clean(request.form.get('description_en'))
            desc_es = _clean(request.form.get('description_es'))
            s.description = desc_en or ''
            s.description_en = desc_en or None
            s.description_es = desc_es or None
            loc_en = _clean(request.form.get('location_en'))
            loc_es = _clean(request.form.get('location_es'))
            s.location = loc_en or ''
            s.location_en = loc_en or None
            s.location_es = loc_es or None
            contact_en = _clean(request.form.get('contact_en'))
            contact_es = _clean(request.form.get('contact_es'))
            s.contact = contact_en or ''
            s.contact_en = contact_en or None
            s.contact_es = contact_es or None
            note_en = _clean(request.form.get('schedule_note_en'))
            note_es = _clean(request.form.get('schedule_note_es'))
            s.schedule_note = note_en or ''
            s.schedule_note_en = note_en or None
            s.schedule_note_es = note_es or None
            s.external_link = _clean(request.form.get('external_link')) or ''
            db.commit()
            audit_log(
                "service.update",
                actor=audit_actor(),
                obj=s.id,
                before=before_data,
                after={
                    "name": s.name,
                    "category": s.category,
                    "availability": s.availability,
                    "is_offsite": s.is_offsite,
                },
            )
            flash(_('Service updated.'), 'success')
            return redirect(url_for('admin_services'))
        return render_template('admin/service_edit.html', service=s)

    @app.route('/admin/services/<int:sid>/delete', methods=['POST'])
    @permission_required('services.edit')
    def admin_services_delete(sid: int):
        """Delete a service and emit an audit log entry."""
        db = dbs()
        s = db.get(Service, sid)
        if s:
            before_data = {
                "name": s.name,
                "category": s.category,
            }
            db.delete(s)
            db.commit()
            audit_log(
                "service.delete",
                actor=audit_actor(),
                obj=sid,
                before=before_data,
            )
            flash(_('Service deleted.'), 'info')
        return redirect(url_for('admin_services'))

    # announcements
    @app.route('/admin/announcements')
    @permission_required('services.view')
    def admin_announcements():
        """List announcements for review in the admin console."""
        db = dbs()
        rows = db.query(Announcement).order_by(Announcement.starts_at.desc()).all()
        return render_template('admin/announcements.html', rows=rows)

    @app.route('/admin/announcements/new', methods=['GET', 'POST'])
    @permission_required('services.edit')
    def admin_announcements_new():
        """Create a new time-bound announcement."""
        if request.method == 'POST':
            db = dbs()
            start = datetime.strptime(
                request.form.get('starts_at'), '%Y-%m-%dT%H:%M'
            ) if request.form.get('starts_at') else datetime.utcnow()
            end = datetime.strptime(
                request.form.get('ends_at'), '%Y-%m-%dT%H:%M'
            ) if request.form.get('ends_at') else None
            a = Announcement(
                title=request.form.get('title') or 'Announcement',
                body=request.form.get('body') or '',
                starts_at=start,
                ends_at=end,
            )
            db.add(a)
            db.commit()
            flash(_('Announcement posted.'), 'success')
            return redirect(url_for('admin_announcements'))
        return render_template('admin/announcements_new.html')

    @app.route('/admin/announcements/<int:aid>/delete', methods=['POST'])
    @permission_required('services.edit')
    def admin_announcements_delete(aid: int):
        """Delete an announcement from the schedule."""
        db = dbs()
        a = db.get(Announcement, aid)
        if a:
            db.delete(a)
            db.commit()
            flash(_('Announcement deleted.'), 'info')
        return redirect(url_for('admin_announcements'))

    # submissions
    @app.route('/admin/submissions')
    @permission_required('submissions.view')
    def admin_submissions():
        """List recent submissions with optional filtering by kind."""
        db = dbs()
        kind = request.args.get('kind')
        q = db.query(Submission)
        if kind:
            q = q.filter(Submission.kind == kind)
        rows = q.order_by(Submission.created_at.desc()).limit(500).all()
        return render_template('admin/submissions.html', rows=rows, kind=kind)

    @app.route('/admin/submissions/<int:sid>')
    @permission_required('submissions.view')
    def admin_submission_detail(sid: int):
        """Show submission details plus any uploaded attachments."""
        db = dbs()
        s = db.get(Submission, sid)
        if not s:
            abort(404)
        upload_root = Path(DATA_DIR) / 'uploads' / s.kind / str(s.id)
        attachments = []
        if upload_root.exists() and upload_root.is_dir():
            for child in sorted(upload_root.iterdir()):
                if child.is_file():
                    try:
                        stat = child.stat()
                        attachments.append({
                            'name': child.name,
                            'size': stat.st_size,
                            'size_label': human_filesize(stat.st_size),
                        })
                    except Exception:
                        continue
        gcase = None
        if s.kind == 'grievance':
            gcase = db.query(GrievanceCase).filter(GrievanceCase.submission_id == s.id).first()
        return render_template('admin/submission_detail.html', s=s, attachments=attachments, grievance_case=gcase)

    @app.route('/admin/submissions/<int:sid>/attachments/<path:filename>')
    @permission_required('submissions.view')
    def admin_submission_attachment(sid: int, filename: str):
        """Send back a stored attachment for a submission."""
        db = dbs()
        s = db.get(Submission, sid)
        if not s:
            abort(404)
        upload_root = Path(DATA_DIR) / 'uploads' / s.kind / str(s.id)
        target = (upload_root / filename).resolve()
        try:
            upload_root_resolved = upload_root.resolve()
        except FileNotFoundError:
            abort(404)
        if not str(target).startswith(str(upload_root_resolved)) or not target.is_file():
            abort(404)
        from flask import send_file
        return send_file(target, download_name=target.name)

    @app.route('/admin/data-tools')
    @roles_required('admin')
    def admin_data_tools():
        """Provide operational summaries (submission counts, uploads, arcade stats)."""
        db = dbs()
        submission_total = db.query(func.count(Submission.id)).scalar() or 0
        score_rows = (
            db.query(GameScore.game, func.count(GameScore.id))
            .group_by(GameScore.game)
            .order_by(GameScore.game)
            .all()
        )
        leaderboard = [
            {"game": (row[0] or ""), "count": int(row[1] or 0)}
            for row in score_rows
        ]
        uploads_root = Path(DATA_DIR) / 'uploads'
        upload_summary: list[dict[str, object]] = []
        if uploads_root.exists():
            try:
                for kind_dir in sorted([p for p in uploads_root.iterdir() if p.is_dir()], key=lambda p: p.name):
                    try:
                        entry_count = sum(1 for _ in kind_dir.iterdir() if _.is_dir())
                    except Exception:
                        entry_count = None
                    upload_summary.append({
                        "kind": kind_dir.name,
                        "folders": entry_count,
                    })
            except Exception as exc:
                current_app.logger.warning('Failed to summarize uploads: %s', exc)
        return render_template(
            'admin/data_tools.html',
            submission_total=int(submission_total),
            upload_summary=upload_summary,
            leaderboard=leaderboard,
        )

    @app.route('/admin/submissions/clear', methods=['POST'])
    @roles_required('admin')
    def admin_submissions_clear():
        """Erase all submissions and associated uploads."""
        db = dbs()
        total = db.query(func.count(Submission.id)).scalar() or 0
        if total == 0:
            flash(_('No submissions to delete.'), 'info')
            return redirect(url_for('admin_data_tools'))
        try:
            db.query(Submission).delete(synchronize_session=False)
            db.commit()
        except Exception as exc:
            db.rollback()
            current_app.logger.exception('Failed to clear submissions: %s', exc)
            flash(_('Could not clear submissions. Check logs for details.'), 'danger')
            return redirect(url_for('admin_data_tools'))
        uploads_root = Path(DATA_DIR) / 'uploads'
        cleared_dirs = 0
        if uploads_root.exists():
            for child in uploads_root.iterdir():
                if child.is_dir():
                    try:
                        shutil.rmtree(child)
                        cleared_dirs += 1
                    except Exception as exc:
                        current_app.logger.warning('Failed to remove upload folder %s: %s', child, exc)
        audit_log(
            'submissions.clear',
            actor=audit_actor(),
            extra={'count': int(total), 'upload_dirs_removed': cleared_dirs},
        )
        flash(
            f'Removed {int(total)} submissions and cleared {cleared_dirs} attachment folders.',
            'success',
        )
        return redirect(url_for('admin_data_tools'))

    @app.route('/admin/arcade/scores/<string:game>/clear', methods=['POST'])
    @roles_required('admin')
    def admin_arcade_clear(game: str):
        """Reset leaderboard entries for the specified arcade game."""
        game_key = (game or '').strip().lower()
        if not game_key:
            abort(400)
        db = dbs()
        existing = db.query(GameScore).filter(GameScore.game == game_key)
        total = existing.count()
        if total == 0:
            flash(f'No scores to clear for {game_key}.', 'info')
            return redirect(url_for('admin_data_tools'))
        try:
            existing.delete(synchronize_session=False)
            db.commit()
        except Exception as exc:
            db.rollback()
            current_app.logger.exception('Failed to clear scores for %s: %s', game_key, exc)
            flash(_('Could not clear leaderboard. Check logs for details.'), 'danger')
            return redirect(url_for('admin_data_tools'))
        audit_log(
            'arcade.clear',
            actor=audit_actor(),
            obj=game_key,
            extra={'count': int(total)},
        )
        flash(f'Cleared {int(total)} scores for {game_key}.', 'success')
        return redirect(url_for('admin_data_tools'))

    # user management
    @app.route('/admin/users')
    @permission_required('admin.users.manage')
    def admin_users():
        """List user accounts for approval or role management."""
        db = dbs()
        users = db.query(User).order_by(User.approved.asc(), User.role.desc(), User.username).all()
        return render_template('admin/users.html', users=users)

    @app.route('/admin/users/new', methods=['GET', 'POST'])
    @permission_required('admin.users.manage')
    def admin_users_new():
        """Create a new staff account from the admin interface."""
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip()
            password = request.form.get('password') or ''
            role = (request.form.get('role') or 'viewer').strip()
            if not username or not password:
                flash(_('Username and password are required.'), 'danger')
                return render_template('admin/user_new.html', form=request.form)
            if role not in ['viewer', 'editor', 'admin']:
                flash(_('Invalid role.'), 'danger')
                return render_template('admin/user_new.html', form=request.form)
            db = dbs()
            if db.query(User).filter(User.username == username).first():
                flash(_('Username already exists.'), 'danger')
                return render_template('admin/user_new.html', form=request.form)
            u = User(
                username=username,
                role=role,
                password_hash=generate_password_hash(password),
                approved=True,
            )
            db.add(u)
            db.commit()
            audit_log(
                "user.create",
                actor=audit_actor(),
                obj=u.id,
                after={"username": u.username, "role": u.role},
            )
            flash(_('User created.'), 'success')
            return redirect(url_for('admin_users'))
        return render_template('admin/user_new.html', form={})

    @app.route('/admin/users/<int:uid>/delete', methods=['POST'])
    @permission_required('admin.users.manage')
    def admin_users_delete(uid: int):
        """Remove a user account and audit the action."""
        db = dbs()
        u = db.get(User, uid)
        if not u:
            abort(404)
        if u.id == session.get('user_id'):
            flash(_("You can't delete yourself."), 'warning')
            return redirect(url_for('admin_users'))
        before_data = {"username": u.username, "role": u.role}
        db.delete(u)
        db.commit()
        audit_log(
            "user.delete",
            actor=audit_actor(),
            obj=uid,
            before=before_data,
        )
        flash(_('User deleted.'), 'success')
        return redirect(url_for('admin_users'))

    @app.route('/admin/users/<int:uid>/update', methods=['POST'])
    @permission_required('admin.users.manage')
    def admin_users_update(uid: int):
        """Update user role/approval status and optionally reset the password."""
        db = dbs()
        u = db.get(User, uid)
        if not u:
            abort(404)
        before_data = {
            "role": u.role,
            "approved": getattr(u, 'approved', None),
        }
        role = (request.form.get('role') or u.role).strip()
        if role not in ['viewer', 'editor', 'admin']:
            flash(_('Invalid role.'), 'danger')
            return redirect(url_for('admin_users'))
        approved_val = (request.form.get('approved') or '').lower()
        approved = approved_val in ['1', 'true', 'on', 'yes']
        u.role = role
        try:
            u.approved = approved
        except Exception:
            # In case column doesn't exist for any reason, ignore quietly
            pass
        db.commit()
        audit_log(
            "user.update",
            actor=audit_actor(),
            obj=uid,
            before=before_data,
            after={
                "role": u.role,
                "approved": getattr(u, 'approved', None),
            },
        )
        flash(_('User updated.'), 'success')
        return redirect(url_for('admin_users'))

    @app.route('/admin/users/<int:uid>/permissions', methods=['GET', 'POST'])
    @permission_required('admin.users.manage')
    def admin_user_permissions(uid: int):
        """Edit a user's checkbox permissions (admins bypass all checks)."""
        db = dbs()
        u = db.get(User, uid)
        if not u:
            abort(404)
        if request.method == 'POST':
            before = sorted(get_permissions(db, u.id))
            granted = set_permissions(db, u.id, request.form.getlist('permissions'))
            db.commit()
            audit_log(
                "user.permissions.update",
                actor=audit_actor(),
                obj=u.id,
                before={"permissions": before},
                after={"permissions": sorted(granted)},
            )
            flash(_('Permissions updated for %(name)s.', name=u.username), 'success')
            return redirect(url_for('admin_users'))
        return render_template(
            'admin/user_permissions.html',
            u=u,
            groups=PERMISSION_GROUPS,
            presets=PRESETS,
            granted=get_permissions(db, u.id),
        )

    @app.template_filter('dt')
    def fmt_dt(v):
        """Format datetimes for templates, tolerating ISO strings."""
        if not v:
            return ''
        try:
            return v.strftime('%Y-%m-%d %H:%M')
        except Exception:
            # accept ISO strings
            try:
                from datetime import datetime as _dt
                dt = _dt.fromisoformat(str(v).replace('Z','+00:00'))
                return dt.strftime('%Y-%m-%d %H:%M')
            except Exception:
                return str(v)

    # ---- Admin PDF Template Management ----
    # (Removed) legacy PDF templates/bindings admin endpoints

    # ---- Per-form PDF Editor (simplified) ----
    @app.get('/admin/forms/<form_key>/pdf')
    @permission_required('pdf_forms.view')
    def admin_form_pdf(form_key: str):
        """Render the PDF editor for a particular submission form."""
        key = (form_key or '').strip().lower()
        db = dbs()
        cfg = db.query(FormPDFConfig).filter(FormPDFConfig.form_key == key).first()
        # Choose a file path: configured or static fallback or DATA_DIR fallback
        static_path = os.path.join(os.path.dirname(__file__), 'static', 'pdf', 'templates', f'{key}.pdf')
        data_path = os.path.join(DATA_DIR, 'pdf-templates', f'{key}.pdf')
        file_path = (cfg.template_path if cfg and cfg.template_path else (static_path if os.path.exists(static_path) else data_path))
        file_exists = bool(file_path and os.path.exists(file_path))
        # Page size (CropBox) if file exists
        page_w = 612
        page_h = 792
        if file_exists:
            try:
                from PyPDF2 import PdfReader
                r = PdfReader(file_path)
                if r.pages:
                    pg = r.pages[0]
                    cb = getattr(pg, 'cropbox', None) or pg.mediabox
                    page_w = int(round(float(cb.right - cb.left)))
                    page_h = int(round(float(cb.top - cb.bottom)))
            except Exception:
                pass
        # Convert stored layout (bottom-left points) -> normalized editor layout
        editor_layout = {"pages": [{"fields": {}}]}
        if cfg and cfg.layout_json and file_exists:
            try:
                stored = json.loads(cfg.layout_json)
                fields = {}
                for k, v in (stored or {}).items():
                    if k == 'pad':
                        continue
                    if isinstance(v, (list, tuple)) and len(v) == 2:
                        cx, cy = float(v[0]), float(v[1])
                        fields[k] = {"type": "checkbox", "cx": (cx / page_w), "cy": (1.0 - (cy / page_h)), "size": 0.018}
                    elif isinstance(v, (list, tuple)) and len(v) == 4:
                        x, y, w, h = [float(n) for n in v]
                        fields[k] = {
                            "type": "line" if h <= 18 else "multiline",
                            "x": (x / page_w),
                            "y": (1.0 - ((y + h) / page_h)),
                            "w": (w / page_w),
                            "h": (h / page_h),
                            "size": 10,
                        }
                editor_layout = {"pages": [{"fields": fields}]}
            except Exception:
                pass
        return render_template('admin/form_pdf.html',
                               form_key=key,
                               file_url=(url_for('admin_form_pdf_file', form_key=key) if file_exists else None),
                               page_w=page_w,
                               page_h=page_h,
                               baseline_pad=(cfg.baseline_pad if cfg else 3),
                               attach_to_email=bool(cfg.attach_to_email) if cfg else False,
                               editor_layout_json=json.dumps(editor_layout))

    @app.get('/admin/forms/<form_key>/pdf/file')
    @permission_required('pdf_forms.view')
    def admin_form_pdf_file(form_key: str):
        """Download the current template PDF for the form."""
        key = (form_key or '').strip().lower()
        db = dbs()
        cfg = db.query(FormPDFConfig).filter(FormPDFConfig.form_key == key).first()
        static_path = os.path.join(os.path.dirname(__file__), 'static', 'pdf', 'templates', f'{key}.pdf')
        data_path = os.path.join(DATA_DIR, 'pdf-templates', f'{key}.pdf')
        file_path = (cfg.template_path if cfg and cfg.template_path else (static_path if os.path.exists(static_path) else data_path))
        if not (file_path and os.path.exists(file_path)):
            abort(404)
        from flask import send_file
        return send_file(file_path, mimetype='application/pdf')

    @app.post('/admin/forms/<form_key>/pdf/upload')
    @permission_required('pdf_forms.edit')
    def admin_form_pdf_upload(form_key: str):
        """Upload or replace the base PDF used for rendering a form."""
        key = (form_key or '').strip().lower()
        f = request.files.get('file')
        if not (f and f.filename):
            flash(_('Select a PDF to upload.'), 'danger')
            return redirect(url_for('admin_form_pdf', form_key=key))
        # Save under static/pdf/templates/<form_key>.pdf
        static_dir = os.path.join(os.path.dirname(__file__), 'static', 'pdf', 'templates')
        data_dir = os.path.join(DATA_DIR, 'pdf-templates')
        file_path = None
        # Try saving into static; fall back to DATA_DIR on any failure
        try:
            os.makedirs(static_dir, exist_ok=True)
            file_path = os.path.join(static_dir, f'{key}.pdf')
            f.save(file_path)
        except Exception:
            try:
                os.makedirs(data_dir, exist_ok=True)
            except Exception:
                pass
            file_path = os.path.join(data_dir, f'{key}.pdf')
            f.save(file_path)
        # Upsert config
        db = dbs()
        cfg = db.query(FormPDFConfig).filter(FormPDFConfig.form_key == key).first()
        if not cfg:
            cfg = FormPDFConfig(form_key=key)
            db.add(cfg)
        cfg.template_path = file_path
        db.commit()
        flash(_('Template uploaded.'), 'success')
        return redirect(url_for('admin_form_pdf', form_key=key))

    @app.post('/admin/forms/<form_key>/pdf/save')
    @permission_required('pdf_forms.edit')
    def admin_form_pdf_save(form_key: str):
        """Persist layout coordinates and email attachment preferences."""
        key = (form_key or '').strip().lower()
        db = dbs()
        cfg = db.query(FormPDFConfig).filter(FormPDFConfig.form_key == key).first()
        if not cfg:
            cfg = FormPDFConfig(form_key=key)
            db.add(cfg)
        # Parse normalized layout from editor
        layout_json = request.form.get('layout_json') or (request.get_json(silent=True) or {}).get('layout_json')
        try:
            tmp = json.loads(layout_json or '{}')
        except Exception as e:
            flash(f'Invalid layout JSON: {e}', 'danger')
            return redirect(url_for('admin_form_pdf', form_key=key))
        # Need page size to denormalize
        file_path = cfg.template_path or os.path.join(os.path.dirname(__file__), 'static', 'pdf', 'templates', f'{key}.pdf')
        page_w, page_h = 612, 792
        if file_path and os.path.exists(file_path):
            try:
                from PyPDF2 import PdfReader
                r = PdfReader(file_path)
                if r.pages:
                    pg = r.pages[0]
                    cb = getattr(pg, 'cropbox', None) or pg.mediabox
                    page_w = float(cb.right - cb.left)
                    page_h = float(cb.top - cb.bottom)
            except Exception:
                pass
        fields = (tmp.get('pages') or [{}])[0].get('fields', {}) if isinstance(tmp, dict) else {}
        stored = {}
        for k, spec in (fields or {}).items():
            t = (spec.get('type') if isinstance(spec, dict) else None) or 'line'
            if t == 'checkbox':
                cx = float(spec.get('cx', 0.0)) * page_w
                cy = (1.0 - float(spec.get('cy', 0.0))) * page_h
                stored[k] = [round(cx, 2), round(cy, 2)]
            else:
                x = float(spec.get('x', 0.0)) * page_w
                w = float(spec.get('w', 0.0)) * page_w
                h = float(spec.get('h', 0.0)) * page_h
                y = (1.0 - (float(spec.get('y', 0.0)) + float(spec.get('h', 0.0)))) * page_h
                stored[k] = [round(x, 2), round(y, 2), round(w, 2), round(h, 2)]
        # Save baseline_pad and attach flag
        bp = request.form.get('baseline_pad') or (request.get_json(silent=True) or {}).get('baseline_pad')
        att = request.form.get('attach_to_email') or (request.get_json(silent=True) or {}).get('attach_to_email')
        try:
            cfg.baseline_pad = int(float(bp)) if bp is not None and str(bp).strip() != '' else (cfg.baseline_pad or 3)
        except Exception:
            pass
        cfg.attach_to_email = True if str(att).lower() in ('1','true','yes','on') else False
        cfg.layout_json = json.dumps({**stored, **({"pad": cfg.baseline_pad} if cfg.baseline_pad else {})})
        db.commit()
        flash(_('Layout saved.'), 'success')
        return redirect(url_for('admin_form_pdf', form_key=key))

    @app.get('/admin/forms/<form_key>/pdf/preview')
    @permission_required('pdf_forms.view')
    def admin_form_pdf_preview(form_key: str):
        """Generate a temporary PDF preview using either sample or real data."""
        key = (form_key or '').strip().lower()
        db = dbs()
        cfg = db.query(FormPDFConfig).filter(FormPDFConfig.form_key == key).first()
        if not (cfg and cfg.template_path and os.path.exists(cfg.template_path)):
            return jsonify({"ok": False, "error": "missing template"}), 400
        try:
            sub_id = int(request.args.get('submission_id') or 0)
        except Exception:
            sub_id = 0
        sub = db.get(Submission, sub_id) if sub_id else None
        case_id = build_grievance_case_id(sub.id, sub.created_at) if (sub and key == 'grievance') else None
        # Build payload for preview PDF rendering
        def _payload():
            """Build the data dict passed into the PDF renderer for preview."""
            import datetime as _dt
            if sub:
                data = {
                    'id': case_id if key == 'grievance' else sub.id,
                    'submission_id': sub.id,
                    'todays_date': _dt.datetime.utcnow().strftime('%Y-%m-%d'),
                    'submitted_date': sub.created_at.strftime('%Y-%m-%d'),
                    'submitted_time': format_time_12(sub.created_at),
                    'staff_involved': '',
                    'name': sub.contact_name or '',
                    'phone': sub.contact_info or '',
                    'email': '',
                    'subject': sub.subject or '',
                    'category': sub.category or '',
                    'location': sub.location or '',
                    'description': sub.body or '',
                }
                # Provide sample booleans so checkboxes can be placed
                data.update({
                    'involves_staff': True,
                    'involves_grace_staff': True,
                    'involves_policies': False,
                    'involves_volunteer': False,
                    'involves_other': True,
                    'involves_other_txt': 'Other details',
                })
                if key == 'grievance':
                    data['case_id'] = case_id
                return data

            sample_now = _dt.datetime.utcnow()
            sample_case_id = build_grievance_case_id(0, sample_now) if key == 'grievance' else None
            sample = {
                'id': sample_case_id if key == 'grievance' else 0,
                'submission_id': 0,
                'todays_date': sample_now.strftime('%Y-%m-%d'),
                'submitted_date': sample_now.strftime('%Y-%m-%d'),
                'submitted_time': format_time_12(sample_now),
                'staff_involved': 'Jane Smith',
                'name': 'Jane Doe',
                'phone': '555-123-4567',
                'email': 'jane@example.org',
                'involves_staff': True,
                'involves_grace_staff': True,
                'involves_policies': False,
                'involves_volunteer': True,
                'involves_other': True,
                'involves_other_txt': 'Other details',
                'description': 'Sample description to verify layout.\nSecond line.',
            }
            if key == 'grievance':
                sample['case_id'] = sample_case_id
            return sample
        debug = request.args.get('debug') in ('1','true','True','yes','on')
        from .pdf_render import render_pdf
        try:
            layout = cfg.layout_json or "{}"  # allow preview with no fields yet
            pdf_bytes = render_pdf(cfg.template_path, layout, _payload(), pad=float(cfg.baseline_pad or 3), debug=debug)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        from flask import send_file
        return send_file(io.BytesIO(pdf_bytes), mimetype='application/pdf', as_attachment=False, download_name=f'preview-{key}.pdf')

    @app.get('/admin/forms/pdf')
    @permission_required('pdf_forms.view')
    def admin_forms_pdf_index():
        """List available forms with PDF configuration for quick navigation."""
        db = dbs()
        # Collect known forms + any from submissions
        keys = set(['grievance', 'maintenance', 'suggestion', 'question'])
        try:
            kinds = [row[0] for row in db.query(Submission.kind).distinct().all()]
            for k in kinds:
                if k:
                    keys.add(k.strip().lower())
        except Exception:
            pass
        rows = []
        from PyPDF2 import PdfReader
        for key in sorted(keys):
            cfg = db.query(FormPDFConfig).filter(FormPDFConfig.form_key == key).first()
            static_path = os.path.join(os.path.dirname(__file__), 'static', 'pdf', 'templates', f'{key}.pdf')
            data_path = os.path.join(DATA_DIR, 'pdf-templates', f'{key}.pdf')
            file_path = (cfg.template_path if cfg and cfg.template_path else (static_path if os.path.exists(static_path) else data_path))
            exists = bool(file_path and os.path.exists(file_path))
            page_w = None
            page_h = None
            if exists:
                try:
                    r = PdfReader(file_path)
                    if r.pages:
                        pg = r.pages[0]
                        cb = getattr(pg, 'cropbox', None) or pg.mediabox
                        page_w = int(round(float(cb.right - cb.left)))
                        page_h = int(round(float(cb.top - cb.bottom)))
                except Exception:
                    pass
            fields_count = None
            pad = None
            if cfg and cfg.layout_json:
                try:
                    data = json.loads(cfg.layout_json)
                    pad = data.get('pad') if isinstance(data, dict) else None
                    if isinstance(data, dict):
                        fields_count = sum(1 for k, v in data.items() if k != 'pad' and isinstance(v, (list, tuple)) and (len(v) in (2, 4)))
                except Exception:
                    pass
            rows.append(dict(
                form_key=key,
                has_template=exists,
                template_path=file_path if exists else None,
                page_w=page_w,
                page_h=page_h,
                fields_count=fields_count,
                baseline_pad=(cfg.baseline_pad if cfg else None),
                attach_to_email=bool(cfg.attach_to_email) if cfg else False,
                updated_at=(cfg.updated_at if cfg else None),
            ))
        return render_template('admin/pdf_index.html', rows=rows)

    # Exempt non-admin views from CSRF protection
    admin_endpoints = {
        rule.endpoint for rule in app.url_map.iter_rules() if rule.rule.startswith('/admin')
    }
    for endpoint, view in app.view_functions.items():
        if endpoint in admin_endpoints:
            continue
        csrf.exempt(view)

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5001, debug=True)
