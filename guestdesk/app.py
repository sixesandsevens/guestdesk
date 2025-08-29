from __future__ import annotations
import os
from datetime import datetime, timedelta
import json
import html as htmlmod
from urllib import request as urlreq, error as urlerr
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, g
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from .models import Base, Service, ProgramSlot, Announcement, Submission, User

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
    os.makedirs(DATA_DIR, exist_ok=True)
    db_path = os.path.join(DATA_DIR, "guestdesk.db")
    engine = create_engine(f"sqlite:///{db_path}", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = scoped_session(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))

    def dbs(): return Session()

    # --- lightweight migration: ensure users.approved exists ---
    try:
        with engine.connect() as conn:
            cols = [r[1] for r in conn.exec_driver_sql('PRAGMA table_info(users)').all()]
            if 'approved' not in cols:
                conn.exec_driver_sql('ALTER TABLE users ADD COLUMN approved INTEGER NOT NULL DEFAULT 1')
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
            return render_template('thanks.html', sub=sub)
        return render_template('submit_kind.html', kind=kind, form={})

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
    def admin_index():
        # Admin/editor dashboard
        if session.get('is_admin') or session.get('role') in ('admin', 'editor'):
            db = dbs()
            svc_count = db.query(Service).count()
            ann_count = db.query(Announcement).count()
            sub_count = db.query(Submission).count()
            recents = db.query(Submission).order_by(Submission.created_at.desc()).limit(5).all()
            return render_template('admin/index.html', svc_count=svc_count, ann_count=ann_count, sub_count=sub_count, recents=recents)
        # logged in but not staff/admin -> send to staff login to switch accounts
        if session.get('user_id'):
            flash('Staff access only. Please sign in with a staff account.', 'warning')
            return redirect(url_for('login', next='/admin'))
        # not logged in -> go log in and come back
        return redirect(url_for('login', next='/admin'))

    # --- manage services ---
    @app.route('/admin/services')
    @roles_required('admin', 'editor')
    def admin_services():
        db = dbs()
        rows = db.query(Service).order_by(Service.category, Service.name).all()
        return render_template('admin/services.html', rows=rows)

    @app.route('/admin/services/new', methods=['GET', 'POST'])
    @roles_required('admin', 'editor')
    def admin_services_new():
        if request.method == 'POST':
            db = dbs()
            s = Service(
                name=request.form.get('name') or 'Unnamed',
                category=request.form.get('category') or 'Other',
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
