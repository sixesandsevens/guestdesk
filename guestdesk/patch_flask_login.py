#!/usr/bin/env python3
"""
Idempotent patcher to migrate GuestDesk to Flask-Login without changing your DB.
- Adds Flask-Login setup that reads current user from Flask session each request.
- Fixes admin landing (/admin) and login redirect defaults.
- Replaces roles_required() decorator to use current_user + session fallbacks.
- Removes the stray "Staff Login" anchor on the login page.
Run:   python scripts/patch_flask_login.py /opt/guestdesk
"""

import sys, os, io, re, shutil

def readf(p):  return io.open(p, "r", encoding="utf-8").read()
def writef(p,s): io.open(p, "w", encoding="utf-8").write(s)
def backup(p):
    if os.path.exists(p) and not os.path.exists(p + ".bak"):
        shutil.copy2(p, p + ".bak")

def ensure_imports_app_py(text):
    # Normalize the base Flask import line (dedupe)
    text = re.sub(r"^from\s+flask\s+import\s+.*$",
                  "from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, g",
                  text, flags=re.M)
    # Add Flask-Login imports once
    if "from flask_login import" not in text:
        text = re.sub(r"(^from\s+flask\s+import[^\n]*\n)",
                      r"\1from flask_login import LoginManager, current_user, login_required, logout_user, UserMixin\n",
                      text, flags=re.M)
    return text

def remove_module_level_admin(text):
    # Delete any TOP-LEVEL @app.route('/admin') blocks (no leading indentation)
    out, i, lines = [], 0, text.splitlines(True)
    while i < len(lines):
        line = lines[i]
        if re.match(r"^@app\.route\(['\"]/admin['\"]\)", line):
            i += 1
            # Skip until next top-level def/route/EOF
            while i < len(lines):
                l = lines[i]
                if re.match(r"^(@app\.route\(|def\s+|class\s+|# -----|\Z)", l):
                    break
                i += 1
            continue
        out.append(line); i += 1
    return "".join(out)

def ensure_admin_index_inside_create_app(text):
    # Insert a 4-space indented /admin landing after the staff marker or before "return app"
    if re.search(r"^\s{4}@app\.route\(['\"]/admin['\"]\)", text, re.M):
        return text  # already present
    admin_block = (
        "    # --- Admin landing ---\n"
        "    @app.route('/admin')\n"
        "    def admin_index():\n"
        "        u = getattr(g, 'user', None)\n"
        "        if session.get('is_admin') or (u and getattr(u, 'role', '').lower() in ('admin','editor')) or (\n"
        "            getattr(current_user, 'is_authenticated', False) and getattr(current_user, 'role', '').lower() in ('admin','editor')\n"
        "        ):\n"
        "            return redirect('/admin/services')\n"
        "        return redirect(url_for('login', next='/admin'))\n"
    )
    if "    # ----- Staff auth & admin -----" in text:
        return text.replace("    # ----- Staff auth & admin -----",
                            "    # ----- Staff auth & admin -----\n" + admin_block)
    # fallback: insert before return app
    return re.sub(r"^\s{4}return app", admin_block + "    return app", text, flags=re.M)

def ensure_login_manager_block(text):
    # Insert a login manager + request_loader right after "app = Flask(__name__" inside create_app
    if "LoginManager(" in text and "request_loader" in text:
        return text
    # Find a reasonable anchor
    m = re.search(r"^\s{4}app\s*=\s*Flask\([^\n]*\)\s*$", text, flags=re.M)
    if not m:
        # fallback: after create_app() def line
        m = re.search(r"^def\s+create_app\([^\)]*\):\s*$", text, flags=re.M)
    if not m:
        return text  # can't find anchor, skip

    insert_at = m.end()
    shim = (
        "\n"
        "    # --- Flask-Login setup (session-backed) ---\n"
        "    login_manager = LoginManager()\n"
        "    login_manager.login_view = 'login'\n"
        "    login_manager.init_app(app)\n"
        "\n"
        "    class UserShim(UserMixin):\n"
        "        def __init__(self, uid, username=None, role='viewer', is_admin=False):\n"
        "            self.id = str(uid)\n"
        "            self.username = username or str(uid)\n"
        "            self.role = (role or 'viewer')\n"
        "            self.is_admin = bool(is_admin)\n"
        "        @property\n"
        "        def is_authenticated(self):\n"
        "            return True\n"
        "\n"
        "    @login_manager.request_loader\n"
        "    def load_user_from_request(req):\n"
        "        # Rehydrate from our existing session flags\n"
        "        uid = session.get('user_id')\n"
        "        if not uid:\n"
        "            return None\n"
        "        return UserShim(\n"
        "            uid=uid,\n"
        "            username=session.get('username'),\n"
        "            role=(session.get('role') or (getattr(g,'user',None).role if getattr(g,'user',None) else 'viewer')),\n"
        "            is_admin=(session.get('is_admin') or session.get('admin')),\n"
        "        )\n"
    )
    return text[:insert_at] + shim + text[insert_at:]

def replace_roles_required(text):
    # Replace the implementation of roles_required() with a Flask-Login aware version
    patt = re.compile(
        r"(^\s{4}def\s+roles_required\([^\)]*\):.*?^\s{4}return\s+_wrap\s*$)",
        flags=re.M | re.S
    )
    new_impl = (
        "    def roles_required(*roles):\n"
        "        from functools import wraps\n"
        "        allowed = tuple(r.lower() for r in roles)\n"
        "        def deco(fn):\n"
        "            @wraps(fn)\n"
        "            def _wrap(*a, **kw):\n"
        "                # Accept one-password admin sessions\n"\
        "                if session.get('is_admin') or session.get('admin'):\n"
        "                    return fn(*a, **kw)\n"
        "                # Flask-Login user\n"
        "                cu = current_user if hasattr(current_user, 'is_authenticated') else None\n"
        "                if cu and cu.is_authenticated:\n"
        "                    if getattr(cu, 'is_admin', False) or getattr(cu, 'role','').lower() in allowed:\n"
        "                        return fn(*a, **kw)\n"
        "                # Legacy g.user\n"
        "                u = getattr(g, 'user', None)\n"
        "                if u and getattr(u, 'role','').lower() in allowed:\n"
        "                    return fn(*a, **kw)\n"
        "                return abort(403)\n"
        "            return _wrap\n"
        "        return deco\n"
    )
    if patt.search(text):
        return patt.sub(new_impl, text)
    # If not found, just append it near the staff auth marker
    return text.replace("    # ----- Staff auth & admin -----",
                        "    # ----- Staff auth & admin -----\n" + new_impl)

def fix_login_redirects(text):
    # url_for('admin') -> url_for('admin_index')
    text = text.replace("url_for('admin')", "url_for('admin_index')").replace(
        'url_for("admin")','url_for("admin_index")'
    )
    # next_url default
    text = re.sub(
        r"(next_url\s*=\s*request\.args\.get\(['\"]next['\"]\))([^\n]*)",
        r"\1 or url_for('admin_index')",
        text
    )
    return text

def patch_app_py(app_py):
    backup(app_py)
    text = readf(app_py)
    text = ensure_imports_app_py(text)
    text = remove_module_level_admin(text)
    text = ensure_admin_index_inside_create_app(text)
    text = ensure_login_manager_block(text)
    text = replace_roles_required(text)
    text = fix_login_redirects(text)
    writef(app_py, text)

def patch_templates_login(login_html):
    if not os.path.exists(login_html):
        return
    backup(login_html)
    s = readf(login_html)
    # remove any explicit "Staff Login" side anchor that links to /admin
    s2 = re.sub(r'\s*<a\s+href="/admin"[^>]*>.*?Staff\s+Login.*?</a>\s*', "\n", s, flags=re.I|re.S)
    if s2 != s: writef(login_html, s2)

def patch_templates_base(base_html):
    if not os.path.exists(base_html):
        return
    backup(base_html)
    s = readf(base_html)
    # Ensure navbar Staff link goes to /admin (not /login)
    s2 = re.sub(r'href="/login"', 'href="/admin"', s)
    if s2 != s: writef(base_html, s2)

def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "/opt/guestdesk"
    app_py = os.path.join(root, "guestdesk", "app.py")
    base_html = os.path.join(root, "guestdesk", "templates", "base.html")
    login_html = os.path.join(root, "guestdesk", "templates", "login.html")

    if not os.path.exists(app_py):
        print("ERROR: app.py not found at", app_py)
        sys.exit(2)

    patch_app_py(app_py)
    patch_templates_base(base_html)
    patch_templates_login(login_html)

    print("Patch complete.")
    print(" - Backups (*.bak) created next to edited files.")
    print(" - Restart your app after clearing __pycache__:")
    print("     find guestdesk -name __pycache__ -type d -exec rm -rf {} +")
    print("     systemctl restart guestdesk")

if __name__ == "__main__":
    main()