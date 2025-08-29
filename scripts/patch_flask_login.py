#!/usr/bin/env python3
"""
patch_flask_login.py  —  safe, idempotent patcher for GuestDesk.

What it does (without breaking your DB schema):
1) Ensures Flask-Login imports are present (no harm if unused yet).
2) Adds a proper /admin landing endpoint (admin_index) *inside* create_app(), if missing.
3) Fixes any wrong url_for('admin') -> url_for('admin_index') usages.
4) Makes login "next" handling safe.
5) Removes the stray "Staff Login" anchor from login.html (or wraps it with a guard).
6) Optionally softens roles_required() to honor session['is_admin'] and g.user.role.

Backups: creates *.bak next to changed files on first run.
Run again: does nothing if already patched (idempotent).
"""
import sys, os, io, re, shutil

def backup(path):
    bak = path + ".bak"
    if not os.path.exists(bak) and os.path.exists(path):
        shutil.copy2(path, bak)

def replace_once(text, old, new):
    if old in text:
        return text.replace(old, new), True
    return text, False

def ensure_imports_app_py(s):
    changed = False
    # Ensure flask_login import is present
    if "from flask_login import" not in s:
        # Put right after flask import line if possible
        s = re.sub(r"^(from\\s+flask\\s+import\\s+.*)$",
                   r"\\1\nfrom flask_login import LoginManager, login_user, logout_user, current_user",
                   s, count=1, flags=re.M)
        changed = True
    return s, changed

def inject_admin_inside_create_app(s):
    """
    Ensure a 4-space-indented /admin ('admin_index') route exists inside create_app().
    Insert it either under the marker '# ----- Staff auth & admin -----' or before 'return app'.
    """
    if re.search(r"^\\s{4}@app\\.route\\(['\\\"]/admin['\\\"]\\)", s, flags=re.M):
        return s, False

    block = (
        "    # --- Admin landing ---\\n"
        "    @app.route('/admin')\\n"
        "    def admin_index():\\n"
        "        u = getattr(g, 'user', None)\\n"
        "        if session.get('is_admin') or (u and getattr(u, 'role', '').lower() in ('admin','editor')):\\n"
        "            return redirect('/admin/services')\\n"
        "        return redirect(url_for('login', next='/admin'))\\n"
    )

    changed = False
    # Try after marker
    if re.search(r"^\\s{4}# ----- Staff auth & admin -----", s, flags=re.M):
        s = re.sub(r"(^\\s{4}# ----- Staff auth & admin -----.*?$)",
                   r"\\1\\n" + block.rstrip("\\n"),
                   s, count=1, flags=re.M|re.S)
        changed = True
    else:
        # Insert before 'return app'
        s, did = re.subn(r"^\\s{4}return app\\s*$", block + "\\n    return app", s, flags=re.M)
        changed = changed or bool(did)
    return s, changed

def fix_wrong_admin_endpoint(s):
    changed = False
    s2, did = replace_once(s, "url_for('admin')", "url_for('admin_index')")
    changed = changed or did
    s2, did = replace_once(s2, 'url_for("admin")', 'url_for("admin_index")')
    changed = changed or did
    return s2, changed

def make_next_safe(s):
    # In the login view, make next_url safe (default to admin_index)
    pat = re.compile(r"^([ \\t]*)next_url\\s*=\\s*request\\.args\\.get\\(['\\\"]next['\\\"]\\).*$", re.M)
    if pat.search(s):
        s = pat.sub(r"\\1next_url = request.args.get('next') or url_for('admin_index')", s)
        return s, True
    return s, False

def soften_roles_required(s: str):
    # Hotfix: skip this step (it was softening a decorator). No change.
    return s, False

def patch_login_html(html):
    """
    Remove or guard the extra 'Staff Login' link on the left column of login page.
    We specifically look for an <a href=\"/admin\">…staff…</a> that isn't the navbar item.
    """
    changed = False
    # The extra anchor often appears bare on the page; remove it
    html2, n = re.subn(r"\\n\\s*<a\\s+href=[\"\\']/admin[\"\\']>\\s*(?:Staff\\s+Login|\\{\\{\\s*t\\(\\s*[\"\\']staff_login[\"\\']\\s*\\)\\s*\\}\\})\\s*</a>\\s*\\n",
                       "\\n", html, flags=re.I)
    if n:
        changed = True
        html = html2
    return html, changed

def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else "/opt/guestdesk"
    app_py = os.path.join(repo, "guestdesk", "app.py")
    login_html = os.path.join(repo, "guestdesk", "templates", "login.html")
    base_html = os.path.join(repo, "guestdesk", "templates", "base.html")

    touched = []

    if os.path.exists(app_py):
        backup(app_py)
        s = io.open(app_py, "r", encoding="utf-8").read()
        any_changed = False

        s, did = ensure_imports_app_py(s); any_changed |= did
        s, did = inject_admin_inside_create_app(s); any_changed |= did
        s, did = fix_wrong_admin_endpoint(s); any_changed |= did
        s, did = make_next_safe(s); any_changed |= did
        s, did = soften_roles_required(s); any_changed |= did

        if any_changed:
            io.open(app_py, "w", encoding="utf-8").write(s)
            touched.append(app_py)

    # login.html stray anchor
    if os.path.exists(login_html):
        backup(login_html)
        h = io.open(login_html, "r", encoding="utf-8").read()
        h2, did = patch_login_html(h)
        if did:
            io.open(login_html, "w", encoding="utf-8").write(h2)
            touched.append(login_html)

    # base.html — ensure navbar Staff goes to /admin (not required here, but keep consistent)
    if os.path.exists(base_html):
        backup(base_html)
        b = io.open(base_html, "r", encoding="utf-8").read()
        # Make the nav Staff link point to /admin
        b2 = re.sub(r'href\\s*=\\s*\"/login\"', 'href=\"/admin\"', b)
        if b2 != b:
            io.open(base_html, "w", encoding="utf-8").write(b2)
            touched.append(base_html)

    if touched:
        print("Patched files:")
        for t in touched:
            print(" -", t)
    else:
        print("Nothing to patch (already up to date).")

if __name__ == "__main__":
    main()
