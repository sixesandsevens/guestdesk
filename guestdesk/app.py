from __future__ import annotations
import os
from datetime import datetime, timedelta
import json
import html as htmlmod
from urllib import request as urlreq, error as urlerr
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, g, jsonify, current_app
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, scoped_session
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from .models import Base, Service, ProgramSlot, Announcement, Submission, User, ServiceSeries, ServiceOverride, Setting
from guestdesk.analytics import init_analytics
from .services_calendar import expand_between
from .mailer import send_category_notification, send_mail

DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")
# Support both the historical and the clearer env var names
DATA_DIR = (
    os.environ.get("GUESTDESK_DATA_DIR")
    or os.environ.get("GUESTD_DATA_DIR")
    or "/var/lib/guestdesk"
)

# Basic i18n for UI strings (English/Spanish); content remains as entered.
STRINGS = {
    'en': {
        'title': 'GuestDesk',
        'services': 'Services',
        'schedule': 'Schedule',
        'announcements': 'Announcements',
        'report_issue': 'Report an Issue',
        'report_maintenance': 'Maintenance Issue',
        'report_grievance': 'File a Grievance',
        'report_suggestion': 'Suggestion / Idea',
        'report_question': 'Ask a Question',
        'fun_zone': 'Fun Zone',
        'staff_login': 'Staff Login',
        'logout': 'Logout',
        'welcome': 'Welcome',
        'today': 'Today',
        'submit': 'Submit',
        'thanks': 'Thanks! Your submission was received. Your reference number is',
        'anonymous_ok': 'You can leave your name/contact blank to stay anonymous.',
        'admin': 'Admin',
        'no_items': 'Nothing here yet. Check back soon.'
    },
    'es': {
        'title': 'GuestDesk',
        'services': 'Servicios',
        'schedule': 'Horario',
        'announcements': 'Anuncios',
        'report_issue': 'Reportar un problema',
        'report_maintenance': 'Problema de mantenimiento',
        'report_grievance': 'Presentar una queja',
        'report_suggestion': 'Sugerencia / Idea',
        'report_question': 'Hacer una pregunta',
        'fun_zone': 'Zona Divertida',
        'staff_login': 'Acceso del personal',
        'logout': 'Cerrar sesión',
        'welcome': 'Bienvenido',
        'today': 'Hoy',
        'submit': 'Enviar',
        'thanks': '¡Gracias! Hemos recibido su envío. Su número de referencia es',
        'anonymous_ok': 'Puede dejar su nombre/contacto en blanco para permanecer anónimo.',
        'admin': 'Admin',
        'no_items': 'Nada aquí todavía. Vuelva pronto.'
    }
}

def t(key):
    lang = session.get('lang', 'en')
    return STRINGS.get(lang, STRINGS['en']).get(key, key)

def create_app():
    app = Flask(__name__)
    # Load env-driven configuration (e.g., mail settings)
    try:
        app.config.from_object("guestdesk.config.Config")
    except Exception:
        # Safe to continue if config module is missing
        pass
    # Email is handled via guestdesk.mailer using env-driven SMTP; no Flask-Mail init needed.
    # Feature flags (available to templates)
    app.config.setdefault("SHOW_HOME_SERVICES", False)
    # --- Jinja filter: "HH:MM" (24h) -> "h:MM AM/PM"
    def h12(t: str) -> str:
        if not t:
            return ""
        try:
            parts = (t or "").split(":")
            h = int(parts[0])
            m = int(parts[1])
            ap = "AM" if h < 12 else "PM"
            h = (h % 12) or 12
            return f"{h}:{m:02d} {ap}"
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
    Session = scoped_session(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    # Initialize analytics blueprint (and ensure table exists)
    try:
        init_analytics(app, engine)
    except Exception:
        # Keep app running even if analytics init fails
        pass

    def dbs(): return Session()

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
        load_user()

    @app.context_processor
    def inject_globals():
        return dict(
            t=t,
            lang=session.get('lang', 'en'),
            user_name=session.get('username'),
            user_role=session.get('role'),
        )

    @app.context_processor
    def inject_flags():
        # makes SHOW_HOME_SERVICES available in all templates
        return dict(SHOW_HOME_SERVICES=app.config["SHOW_HOME_SERVICES"])

    @app.context_processor
    def inject_asset_version():
        # expose ASSET_VERSION for cache-busting static assets
        return dict(ASSET_VERSION=app.config.get("ASSET_VERSION", "1"))

    @app.route('/lang/<code>')
    def set_lang(code):
        session['lang'] = 'es' if code == 'es' else 'en'
        return redirect(request.referrer or url_for('home'))

    @app.route('/')
    def home():
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
        db = dbs()
        cat = request.args.get('cat')
        q = db.query(Service)
        if cat:
            q = q.filter(Service.category == cat)
        rows = q.order_by(Service.category, Service.name).all()
        return render_template('services.html', rows=rows, cat=cat)

    @app.route('/service/<int:sid>')
    def service_detail(sid:int):
        db = dbs()
        s = db.get(Service, sid)
        if not s:
            abort(404)
        days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
        return render_template('service_detail.html', s=s, days=days)

    @app.route('/schedule')
    def schedule():
        db = dbs()
        days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
        services = db.query(Service).order_by(Service.category, Service.name).all()
        # Build weekly matrix
        matrix = {i: [] for i in range(7)}
        for svc in services:
            for slot in svc.slots:
                matrix[slot.dow].append((svc, slot))
        return render_template('schedule.html', days=days, matrix=matrix)

    @app.route('/announcements')
    def announcements():
        db = dbs()
        now = datetime.utcnow()
        anns = db.query(Announcement).filter(Announcement.starts_at <= now).filter(
            (Announcement.ends_at.is_(None)) | (Announcement.ends_at >= now)
        ).order_by(Announcement.starts_at.desc()).all()
        return render_template('announcements.html', anns=anns)

    # ----- Submissions (guest) -----
    @app.route('/report')
    def report():
        return render_template('report.html')

    @app.route('/submit/<kind>', methods=['GET','POST'])
    def submit(kind):
        if kind not in ['maintenance','grievance','suggestion','question']:
            abort(404)
        if request.method == 'POST':
            body = (request.form.get('body') or '').strip()
            if not body:
                flash('Please add some details.', 'danger')
                return render_template('submit_kind.html', kind=kind, form=request.form)
            sub = Submission(
                kind=kind,
                subject=(request.form.get('subject') or '').strip() or None,
                body=body,
                category=(request.form.get('category') or '').strip() or None,
                building=(request.form.get('building') or '').strip() or None,
                location=(request.form.get('location') or '').strip() or None,
                contact_name=(request.form.get('contact_name') or '').strip() or None,
                contact_info=(request.form.get('contact_info') or '').strip() or None,
            )
            db = dbs()
            db.add(sub)
            db.commit()
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

                default_subjects = {
                    'maintenance': 'GuestDesk: Maintenance Issue',
                    'grievance': 'GuestDesk: Grievance',
                    'suggestion': 'GuestDesk: Suggestion / Idea',
                    'question': 'GuestDesk: Question',
                }
                subject = (request.form.get('subject') or '').strip() or default_subjects.get(kind, 'GuestDesk: Submission')

                if kind == 'grievance':
                    # Lazy import so app can boot even if PDF libs missing
                    try:
                        from .utils.grievance_pdf import render_grievance_pdf, generate_grv_id
                    except Exception as _imp_err:
                        app.logger.warning('Grievance PDF dependencies not available: %s', _imp_err)
                        render_grievance_pdf = None
                        def generate_grv_id(now=None):
                            now = now or _dt.datetime.utcnow()
                            return f"GRV-{now.strftime('%Y')}-{int(now.timestamp())}"
                    # Generate PDF and email to configured recipients
                    import datetime as _dt
                    now = _dt.datetime.utcnow()
                    y, m = now.strftime('%Y'), now.strftime('%m')
                    grv_id = generate_grv_id(now)

                    data = {
                        "id": grv_id,
                        "submitted_at": now.strftime('%Y-%m-%d %H:%MZ'),
                        "name": (request.form.get('name') or request.form.get('contact_name') or '').strip(),
                        "phone": (request.form.get('phone') or request.form.get('contact_info') or '').strip(),
                        "email": (request.form.get('email') or '').strip(),
                        "staff_involved": (request.form.get('staff_involved') or '').strip(),
                        "involves": {
                            "grace_staff": bool(request.form.get('involves_grace_staff')),
                            "policies_procedures": bool(request.form.get('involves_policies')),
                            "volunteer": bool(request.form.get('involves_volunteer')),
                            "other_text": (request.form.get('involves_other') or '').strip(),
                        },
                        "incident_date": (request.form.get('incident_date') or '').strip(),
                        "incident_time": (request.form.get('incident_time') or '').strip(),
                        "description": msg_text,
                    }

                    archive_dir = os.path.join(current_app.config.get('GRIEVANCE_ARCHIVE_DIR', '/opt/guestdesk/forms/grievances'), y, m)
                    pdf_path = os.path.join(archive_dir, f"{grv_id}.pdf")

                    pdf_bytes = None
                    if render_grievance_pdf is not None:
                        try:
                            render_grievance_pdf(
                                data,
                                current_app.config.get('GRIEVANCE_TEMPLATE_PDF'),
                                pdf_path,
                            )
                            with open(pdf_path, 'rb') as f:
                                pdf_bytes = f.read()
                        except Exception as _e:
                            app.logger.exception('Failed to render grievance PDF: %s', _e)

                    to_list = [e.strip() for e in current_app.config.get('GRIEVANCE_EMAIL_TO', []) if e and e.strip()]
                    cc_list = [e.strip() for e in current_app.config.get('GRIEVANCE_EMAIL_CC', []) if e and e.strip()]
                    sender = current_app.config.get('GRIEVANCE_FROM')

                    attachments = []
                    if pdf_bytes:
                        attachments.append(("application/pdf", f"{grv_id}.pdf", pdf_bytes))

                    send_mail(
                        subject=f"[GuestDesk] Grievance {grv_id}",
                        body=(
                            "A new grievance has been submitted.\n\n"
                            f"ID: {grv_id}\n"
                            f"Submitted: {data['submitted_at']}\n"
                            f"From: {data['name']} ({data['phone']}, {data['email']})\n"
                        ),
                        to=to_list or [current_app.config.get('GRIEVANCE_EMAIL') or current_app.config.get('ADMIN_EMAIL')],
                        cc=cc_list,
                        sender=sender,
                        attachments=attachments,
                        reply_to=data['email'] or None,
                    )
                else:
                    # Default behavior for other categories
                    send_category_notification(
                        kind,
                        {
                            'name': (request.form.get('contact_name') or '').strip() or None,
                            'email': (request.form.get('email') or '').strip() or None,
                            'phone': (request.form.get('phone') or '').strip() or None,
                            'subject': subject,
                            'message': msg_text,
                            'url': request.url,
                            'extra': extra,
                        },
                        reply_to=(request.form.get('email') or '').strip() or None,
                    )
            except Exception as e:
                app.logger.exception('Failed to send %s email: %s', kind, e)
            return render_template('thanks.html', sub=sub)
        return render_template('submit_kind.html', kind=kind, form={})

    # Development-only mail smoke test
    @app.get('/_mail_test')
    def _mail_test():
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
        uid = session.get('user_id')
        if not uid:
            return None
        db = dbs()
        return db.get(User, uid)

    def login_required(fn):
        @wraps(fn)
        def _wrap(*a, **kw):
            # Let admin session OR a logged-in user through
            if session.get("is_admin") or session.get("admin"):
                return fn(*a, **kw)
            u = getattr(g, "user", None)
            if u:
                return fn(*a, **kw)
            return redirect(url_for("login", next=request.path))
        return _wrap
    def roles_required(*required_roles):
        def deco(fn):
            @wraps(fn)
            def _wrap(*a, **kw):
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
        db = dbs()
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip()
            password = request.form.get('password') or ''
            if not username or not password:
                flash('Username and password required.', 'danger')
                return render_template('signup.html', form=request.form)
            if db.query(User).filter(User.username == username).first():
                flash('Username already exists.', 'danger')
                return render_template('signup.html', form=request.form)
            u = User(
                username=username,
                role='viewer',
                password_hash=generate_password_hash(password),
                approved=False,
            )
            db.add(u)
            db.commit()
            flash('Account created. Awaiting staff approval before login.', 'success')
            return redirect(url_for('home'))
        return render_template('signup.html', form={})

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        # Carry next from query or form so POST preserves it; default to home
        next_url = request.args.get('next') or request.form.get('next') or url_for('home')
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip()
            password = request.form.get('password') or ''
            db = dbs()
            u = db.query(User).filter(User.username == username).first()
            if u and not getattr(u, 'approved', True):
                flash('Account pending approval. Please contact an administrator.', 'warning')
                return render_template('login.html')
            if u and check_password_hash(u.password_hash, password):
                session['user_id'] = u.id
                session['username'] = u.username
                session['role'] = u.role
                flash('Welcome back.', 'success')
                return redirect(next_url)
            flash('Wrong username or password.', 'danger')
        return render_template('login.html')

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect(url_for('home'))

    # --- Admin landing ---
    @app.route('/admin')
    @roles_required('admin', 'editor')
    def admin_index():
        # Admin/editor dashboard
        svc_count = 0
        ann_count = 0
        sub_count = 0
        recents = []
        try:
            db = dbs()
            svc_count = db.query(Service).count()
            ann_count = db.query(Announcement).count()
            sub_count = db.query(Submission).count()
            recents = (
                db.query(Submission)
                .order_by(Submission.created_at.desc())
                .limit(5)
                .all()
            )
        except Exception as e:
            # Log and continue with defaults
            app.logger.exception("Admin dashboard load failed: %s", e)
        return render_template('admin/index.html', svc_count=svc_count, ann_count=ann_count, sub_count=sub_count, recents=recents)

    # --- manage services ---
    @app.route('/admin/analytics')
    @roles_required('admin')
    def admin_analytics():
        # Render dashboard shell; data loads via JSON APIs below
        return render_template('admin/analytics.html')

    # --- Email settings (admin) ---
    @app.route('/admin/email-settings', methods=['GET', 'POST'])
    @roles_required('admin')
    def admin_email_settings():
        db = dbs()
        keys = [
            'MAINTENANCE_EMAIL_TO',
            'GRIEVANCE_EMAIL_TO', 'GRIEVANCE_EMAIL_CC', 'GRIEVANCE_FROM',
            'SUGGESTION_EMAIL_TO', 'QUESTION_EMAIL_TO',
        ]
        if request.method == 'POST':
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
            flash('Email settings updated.', 'success')
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
    def _date_range():
        from datetime import date, timedelta
        s = request.args.get('from')
        e = request.args.get('to')
        if not s or not e:
            end = date.today()
            start = end - timedelta(days=29)
        else:
            from datetime import datetime as _dt
            start = _dt.fromisoformat(s).date()
            end = _dt.fromisoformat(e).date()
        return start, end

    def _between_clause(col: str) -> str:
        return f"{col} >= :start AND {col} < :endp1"

    @app.get('/admin/analytics/api/summary')
    @roles_required('admin')
    def analytics_api_summary():
        start, end = _date_range()
        with engine.connect() as conn:
            res = conn.execute(text(
                f"""
                SELECT COUNT(*) AS total,
                       COUNT(DISTINCT COALESCE(NULLIF(ip_hash,''), client_id)) AS uniques,
                       SUM(CASE WHEN (path LIKE '/submit/%' OR path='/report') THEN 1 ELSE 0 END) AS form_submissions,
                       SUM(CASE WHEN COALESCE(is_staff,0)=1 THEN 1 ELSE 0 END) AS staff
                FROM analytics_events
                WHERE {_between_clause('started_at')}
                """
            ), dict(start=f"{start} 00:00:00", endp1=f"{end} 23:59:59")).mappings().first()
        guests = (res['total'] or 0) - int(res['staff'] or 0)
        return jsonify(dict(total=int(res['total'] or 0), uniques=int(res['uniques'] or 0), form_submissions=int(res['form_submissions'] or 0), staff=int(res['staff'] or 0), guests=guests))

    @app.get('/admin/analytics/api/timeseries')
    @roles_required('admin')
    def analytics_api_timeseries():
        start, end = _date_range()
        with engine.connect() as conn:
            rows = conn.execute(text(
                f"""
                SELECT DATE(started_at) AS d,
                       COUNT(*) AS hits,
                       COUNT(DISTINCT COALESCE(NULLIF(ip_hash,''), client_id)) AS uniques
                FROM analytics_events
                WHERE {_between_clause('started_at')}
                GROUP BY d ORDER BY d
                """
            ), dict(start=f"{start} 00:00:00", endp1=f"{end} 23:59:59")).mappings().all()
        return jsonify([{"date": str(r['d']), "hits": int(r['hits']), "uniques": int(r['uniques'])} for r in rows])

    @app.get('/admin/analytics/api/top-pages')
    @roles_required('admin')
    def analytics_api_top_pages():
        start, end = _date_range()
        with engine.connect() as conn:
            rows = conn.execute(text(
                f"""
                SELECT path, COUNT(*) AS views, ROUND(AVG(duration_ms)) AS avg_ms
                FROM analytics_events
                WHERE {_between_clause('started_at')}
                GROUP BY path ORDER BY views DESC LIMIT 25
                """
            ), dict(start=f"{start} 00:00:00", endp1=f"{end} 23:59:59")).mappings().all()
        return jsonify([{"path": r['path'], "views": int(r['views']), "avg_ms": int(r['avg_ms'] or 0)} for r in rows])

    @app.get('/admin/analytics/api/flows')
    @roles_required('admin')
    def analytics_api_flows():
        start, end = _date_range()
        with engine.connect() as conn:
            rows = conn.execute(text(
                f"""
                SELECT COALESCE(NULLIF(referrer_path,''), referrer) AS src,
                       path AS dst,
                       COUNT(*) AS c
                FROM analytics_events
                WHERE {_between_clause('started_at')} AND COALESCE(referrer_path, referrer) IS NOT NULL
                GROUP BY src, dst ORDER BY c DESC LIMIT 50
                """
            ), dict(start=f"{start} 00:00:00", endp1=f"{end} 23:59:59")).mappings().all()
        return jsonify([{"from": r['src'] or "(direct)", "to": r['dst'], "count": int(r['c'])} for r in rows])

    @app.get('/admin/analytics/api/categories')
    @roles_required('admin')
    def analytics_api_categories():
        start, end = _date_range()
        with engine.connect() as conn:
            rows = conn.execute(text(
                f"""
                SELECT CASE
                         WHEN path LIKE '/admin%%' THEN 'admin'
                         WHEN path LIKE '/fun%%' THEN 'funzone'
                         WHEN path = '/report' OR path LIKE '/submit/%%' THEN 'form'
                         ELSE 'page'
                       END AS cat,
                       COUNT(*) AS c
                FROM analytics_events
                WHERE {_between_clause('started_at')}
                GROUP BY cat
                """
            ), dict(start=f"{start} 00:00:00", endp1=f"{end} 23:59:59")).mappings().all()
        return jsonify([{"category": r['cat'], "count": int(r['c'])} for r in rows])

    @app.get('/admin/analytics/api/forms')
    @roles_required('admin')
    def analytics_api_forms():
        start, end = _date_range()
        with engine.connect() as conn:
            rows = conn.execute(text(
                f"""
                SELECT CASE
                         WHEN path LIKE '/submit/%%' THEN substr(path, length('/submit/')+1)
                         ELSE 'unknown'
                       END AS form,
                       COUNT(*) AS c
                FROM analytics_events
                WHERE {_between_clause('started_at')}
                  AND (path LIKE '/submit/%%' OR path = '/report')
                GROUP BY form ORDER BY c DESC
                """
            ), dict(start=f"{start} 00:00:00", endp1=f"{end} 23:59:59")).mappings().all()
        return jsonify([{"form": r['form'], "count": int(r['c'])} for r in rows])

    @app.get('/admin/analytics/api/perf')
    @roles_required('admin')
    def analytics_api_perf():
        start, end = _date_range()
        with engine.connect() as conn:
            rows = conn.execute(text(
                f"""
                SELECT path, ROUND(AVG(COALESCE(page_load_ms, duration_ms))) AS avg
                FROM analytics_events
                WHERE {_between_clause('started_at')}
                GROUP BY path
                ORDER BY avg DESC
                LIMIT 25
                """
            ), dict(start=f"{start} 00:00:00", endp1=f"{end} 23:59:59")).mappings().all()
        return jsonify([{"path": r['path'], "avg_ms": int(r['avg'] or 0)} for r in rows])

    @app.route('/admin/services')
    @roles_required('admin', 'editor')
    def admin_services():
        db = dbs()
        rows = db.query(Service).order_by(Service.category, Service.name).all()
        return render_template('admin/services.html', rows=rows)

    # ---- Services Calendar ----
    @app.route('/admin/services/calendar')
    @roles_required('admin', 'editor')
    def admin_services_calendar():
        return render_template('admin/services_calendar.html', sid=None)

    @app.route('/admin/services/<int:sid>/calendar')
    @roles_required('admin', 'editor')
    def admin_services_calendar_one(sid:int):
        return render_template('admin/services_calendar.html', sid=sid)

    @app.get('/admin/services/feed')
    @roles_required('admin', 'editor')
    def admin_services_feed():
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

    @app.post('/admin/services/series')
    @roles_required('admin', 'editor')
    def admin_series_create():
        data = request.get_json(force=True)
        db = dbs()
        try:
            s = ServiceSeries(
                title=data.get('title') or 'Untitled Service',
                location=data.get('location'),
                category=data.get('category'),
                notes=data.get('notes'),
                tz=data.get('tz') or 'America/New_York',
                dtstart=(fromiso(data.get('dtstart'))),
                dtend=(fromiso(data.get('dtend'))),
                rrule=data.get('rrule'),
                rdate=data.get('rdate') or [],
                exdate=data.get('exdate') or [],
                is_all_day=bool(data.get('is_all_day', False)),
                is_active=True,
            )
            db.add(s)
            db.commit()
            return jsonify({'id': s.id}), 201
        finally:
            db.close()

    @app.put('/admin/services/series/<int:series_id>')
    @roles_required('admin', 'editor')
    def admin_series_update(series_id:int):
        data = request.get_json(force=True)
        db = dbs()
        try:
            s = db.get(ServiceSeries, series_id)
            if not s:
                abort(404)
            for k in ['title','location','category','notes','tz','rrule','rdate','exdate','is_all_day','is_active']:
                if k in data:
                    setattr(s, k, data[k])
            if 'dtstart' in data:
                s.dtstart = fromiso(data.get('dtstart'))
            if 'dtend' in data:
                s.dtend = fromiso(data.get('dtend'))
            db.commit()
            return jsonify({'ok': True})
        finally:
            db.close()

    @app.post('/admin/services/override')
    @roles_required('admin', 'editor')
    def admin_series_override():
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
        from dateutil.parser import isoparse as _isoparse
        if not s:
            return None
        return _isoparse(s)

    @app.route('/admin/services/new', methods=['GET', 'POST'])
    @roles_required('admin', 'editor')
    def admin_services_new():
        if request.method == 'POST':
            db = dbs()
            s = Service(
                name=request.form.get('name') or 'Unnamed',
                category=request.form.get('category') or 'Other',
                availability=(request.form.get('availability') or 'scheduled'),
                is_offsite=bool(request.form.get('is_offsite')),
                description=request.form.get('description') or '',
                location=request.form.get('location') or '',
                contact=request.form.get('contact') or '',
                schedule_note=request.form.get('schedule_note') or '',
                external_link=request.form.get('external_link') or '',
            )
            db.add(s)
            db.commit()
            flash('Service created.', 'success')
            return redirect(url_for('admin_services'))
        return render_template('admin/services_new.html')

    @app.route('/admin/services/<int:sid>/edit', methods=['GET', 'POST'])
    @roles_required('admin', 'editor')
    def admin_services_edit(sid: int):
        db = dbs()
        s = db.get(Service, sid)
        if not s:
            abort(404)
        if request.method == 'POST':
            s.name = request.form.get('name') or s.name
            s.category = request.form.get('category') or s.category
            s.availability = request.form.get('availability') or s.availability
            s.is_offsite = bool(request.form.get('is_offsite'))
            s.description = request.form.get('description') or ''
            s.location = request.form.get('location') or ''
            s.contact = request.form.get('contact') or ''
            s.schedule_note = request.form.get('schedule_note') or ''
            s.external_link = request.form.get('external_link') or ''
            db.commit()
            flash('Service updated.', 'success')
            return redirect(url_for('admin_services'))
        return render_template('admin/service_edit.html', service=s)

    @app.route('/admin/services/<int:sid>/delete', methods=['POST'])
    @roles_required('admin')
    def admin_services_delete(sid: int):
        db = dbs()
        s = db.get(Service, sid)
        if s:
            db.delete(s)
            db.commit()
            flash('Service deleted.', 'info')
        return redirect(url_for('admin_services'))

    # slots
    @app.route('/admin/services/<int:sid>/slots', methods=['GET', 'POST'])
    @roles_required('admin', 'editor')
    def admin_slots(sid: int):
        db = dbs()
        s = db.get(Service, sid)
        if not s:
            abort(404)
        if request.method == 'POST':
            try:
                dow = int(request.form.get('dow'))
            except Exception:
                dow = 0
            slot = ProgramSlot(
                service_id=s.id,
                dow=dow,
                start=(request.form.get('start') or '').strip() or None,
                end=(request.form.get('end') or '').strip() or None,
                note=(request.form.get('note') or '').strip() or None,
            )
            db.add(slot)
            db.commit()
            flash('Time slot added.', 'success')
            return redirect(url_for('admin_slots', sid=s.id))
        return render_template('admin/slots.html', s=s)

    @app.route('/admin/slots/<int:slot_id>/delete', methods=['POST'])
    @roles_required('admin', 'editor')
    def admin_slot_delete(slot_id: int):
        db = dbs()
        slot = db.get(ProgramSlot, slot_id)
        if slot:
            sid = slot.service_id
            db.delete(slot)
            db.commit()
            flash('Slot deleted.', 'info')
            return redirect(url_for('admin_slots', sid=sid))
        return redirect(url_for('admin_index'))

    # announcements
    @app.route('/admin/announcements')
    @roles_required('admin', 'editor')
    def admin_announcements():
        db = dbs()
        rows = db.query(Announcement).order_by(Announcement.starts_at.desc()).all()
        return render_template('admin/announcements.html', rows=rows)

    @app.route('/admin/announcements/new', methods=['GET', 'POST'])
    @roles_required('admin', 'editor')
    def admin_announcements_new():
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
            flash('Announcement posted.', 'success')
            return redirect(url_for('admin_announcements'))
        return render_template('admin/announcements_new.html')

    @app.route('/admin/announcements/<int:aid>/delete', methods=['POST'])
    @roles_required('admin', 'editor')
    def admin_announcements_delete(aid: int):
        db = dbs()
        a = db.get(Announcement, aid)
        if a:
            db.delete(a)
            db.commit()
            flash('Announcement deleted.', 'info')
        return redirect(url_for('admin_announcements'))

    # submissions
    @app.route('/admin/submissions')
    @roles_required('admin', 'editor')
    def admin_submissions():
        db = dbs()
        kind = request.args.get('kind')
        q = db.query(Submission)
        if kind:
            q = q.filter(Submission.kind == kind)
        rows = q.order_by(Submission.created_at.desc()).limit(500).all()
        return render_template('admin/submissions.html', rows=rows, kind=kind)

    @app.route('/admin/submissions/<int:sid>')
    @roles_required('admin', 'editor')
    def admin_submission_detail(sid: int):
        db = dbs()
        s = db.get(Submission, sid)
        if not s:
            abort(404)
        return render_template('admin/submission_detail.html', s=s)

    # user management
    @app.route('/admin/users')
    @roles_required('admin')
    def admin_users():
        db = dbs()
        users = db.query(User).order_by(User.approved.asc(), User.role.desc(), User.username).all()
        return render_template('admin/users.html', users=users)

    @app.route('/admin/users/new', methods=['GET', 'POST'])
    @roles_required('admin')
    def admin_users_new():
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip()
            password = request.form.get('password') or ''
            role = (request.form.get('role') or 'viewer').strip()
            if not username or not password:
                flash('Username and password are required.', 'danger')
                return render_template('admin/user_new.html', form=request.form)
            if role not in ['viewer', 'editor', 'admin']:
                flash('Invalid role.', 'danger')
                return render_template('admin/user_new.html', form=request.form)
            db = dbs()
            if db.query(User).filter(User.username == username).first():
                flash('Username already exists.', 'danger')
                return render_template('admin/user_new.html', form=request.form)
            u = User(
                username=username,
                role=role,
                password_hash=generate_password_hash(password),
                approved=True,
            )
            db.add(u)
            db.commit()
            flash('User created.', 'success')
            return redirect(url_for('admin_users'))
        return render_template('admin/user_new.html', form={})

    @app.route('/admin/users/<int:uid>/delete', methods=['POST'])
    @roles_required('admin')
    def admin_users_delete(uid: int):
        db = dbs()
        u = db.get(User, uid)
        if not u:
            abort(404)
        if u.id == session.get('user_id'):
            flash("You can't delete yourself.", 'warning')
            return redirect(url_for('admin_users'))
        db.delete(u)
        db.commit()
        flash('User deleted.', 'success')
        return redirect(url_for('admin_users'))

    @app.route('/admin/users/<int:uid>/update', methods=['POST'])
    @roles_required('admin')
    def admin_users_update(uid: int):
        db = dbs()
        u = db.get(User, uid)
        if not u:
            abort(404)
        role = (request.form.get('role') or u.role).strip()
        if role not in ['viewer', 'editor', 'admin']:
            flash('Invalid role.', 'danger')
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
        flash('User updated.', 'success')
        return redirect(url_for('admin_users'))

    @app.template_filter('dt')
    def fmt_dt(v):
        if not v:
            return ''
        return v.strftime('%Y-%m-%d %H:%M')

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5001, debug=True)
