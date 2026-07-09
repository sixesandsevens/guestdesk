---
name: verify
description: Launch GuestDesk locally against a scratch data dir and drive it over HTTP to verify changes end-to-end.
---

# Verifying GuestDesk changes at runtime

## Launch

The app is a Flask factory (`guestdesk.app.create_app`). Point it at a scratch
data dir (SQLite DB + uploads are created there automatically) and run on a
spare port:

```bash
mkdir -p "$SCRATCH/vdata"
cd /home/sixesandsevens/Projects/guestdesk
GUESTDESK_DATA_DIR="$SCRATCH/vdata" .venv/bin/python -c "
from guestdesk.app import create_app
create_app().run(host='127.0.0.1', port=5099)
"   # run in background; poll / until it answers
```

## Seed a user

Fresh DB has no accounts. Seed before launching (same env var):

```python
from guestdesk.app import create_app
from guestdesk.models import User
from werkzeug.security import generate_password_hash
app = create_app()
with app.app_context():
    db = app.dbs()
    db.add(User(username='boss', role='admin',
                password_hash=generate_password_hash('testpass123'), approved=True))
    db.commit()
```

Non-admin accounts also need `UserPermission` rows (role alone grants nothing);
admin role bypasses all permission checks.

## Drive with curl

CSRF is on. Every form POST needs the token from a prior GET on the same
cookie jar:

```bash
J="$SCRATCH/cookies.txt"
TOKEN=$(curl -s -c "$J" http://127.0.0.1:5099/login \
  | grep -o 'name="csrf_token" value="[^"]*"' | sed 's/.*value="//;s/"$//')
curl -s -b "$J" -c "$J" -d "username=boss&password=testpass123&csrf_token=$TOKEN" \
  http://127.0.0.1:5099/login    # 302 -> / on success
```

Re-fetch the token from the target form page before each subsequent POST.
File uploads: `-F "field=@file"` (repeat the flag for multi-file inputs);
override the client filename with `-F "field=@file;filename=other.png"`.

## Gotchas

- No headless browser on this machine — capture rendered HTML instead of
  screenshots.
- Startup logs a `force_secure_cookies=False` warning; harmless locally.
- Uploads land under `$GUESTDESK_DATA_DIR/uploads/<area>/<id>/` — check the
  filesystem directly to confirm storage and cleanup.
