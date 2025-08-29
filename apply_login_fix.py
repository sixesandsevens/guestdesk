import os, re, sys, io

root = sys.argv[1] if len(sys.argv) > 1 else "/opt/guestdesk"
app_py = os.path.join(root, "guestdesk", "app.py")

def backup(path):
    if not os.path.exists(path): return
    bak = path + ".bak"
    if not os.path.exists(bak):
        with open(path,"rb") as r, open(bak,"wb") as w: w.write(r.read())

backup(app_py)
with open(app_py, "r", encoding="utf-8") as f:
    src = f.read()

# Ensure we can import check_password_hash
if "from werkzeug.security import " not in src:
    src = src.replace(
        "from werkzeug.security import generate_password_hash",
        "from werkzeug.security import generate_password_hash, check_password_hash"
    )

# Replace the 4-space-indented login() function block with a robust version.
pattern = re.compile(
    r"(^\s{4}@app\.route\(["]/login[\"].*?\)\s*\n^\s{4}def\s+login\([^\)]*\):\n)(.*?)(?=^\s{4}@app\.route\(|^\s{4}# ----|^\s{4}return app|^\s*$)",
    re.S | re.M
)

new_body = """    @app.route(/login, methods=[GET,POST])
    def login():
        next_url = request.args.get(next) or 
        if request.method == POST:
            apw = os.environ.get(ADMIN_PASSWORD) or 
            # One-password admin
            if (request.form.get(admin_password) or ) == apw and apw:
                session.clear()
                session[is_admin] = True
                flash(Welcome
