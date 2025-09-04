import sqlite3, os, sys

DB_DIRS = ["/var/lib/guestdesk", "/opt/guestdesk", "/opt/guestdesk/guestdesk"]
db_path = None
for base in DB_DIRS:
    for fn in ("guestdesk.db", "app.db", "database.db"):
        p = os.path.join(base, fn)
        if os.path.exists(p):
            db_path = p
            break
    if db_path:
        break

if not db_path:
    print("Could not find sqlite DB; edit this script to point to it.")
    sys.exit(1)

con = sqlite3.connect(db_path)
cur = con.cursor()


def has_col(table, col):
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())


changed = False
if not has_col("services", "availability"):
    cur.execute("ALTER TABLE services ADD COLUMN availability TEXT NOT NULL DEFAULT 'scheduled'")
    changed = True
if not has_col("services", "is_offsite"):
    cur.execute("ALTER TABLE services ADD COLUMN is_offsite INTEGER NOT NULL DEFAULT 0")
    changed = True

con.commit()
print("DB:", db_path, "| changed:", changed)
