#!/usr/bin/env python3
import os
import sys
import datetime as dt

# Make imports work whether run from package dir or its parent
THIS_DIR = os.path.dirname(__file__)
PKG_DIR = os.path.dirname(THIS_DIR)               # .../guestdesk
PARENT_DIR = os.path.dirname(PKG_DIR)             # .../
for p in (PKG_DIR, PARENT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from guestdesk.utils.grievance_pdf import render_grievance_pdf, generate_grv_id
except Exception:
    from utils.grievance_pdf import render_grievance_pdf, generate_grv_id

TEMPLATE = "/opt/guestdesk/guestdesk/static/pdf/Grievance_template.pdf"
OUT_DIR  = "/opt/guestdesk/forms/grievances/test"
os.makedirs(OUT_DIR, exist_ok=True)

now = dt.datetime.utcnow()
grv_id = generate_grv_id(now)
pdf_path = os.path.join(OUT_DIR, f"{grv_id}.pdf")

data = {
    "id": grv_id,
    "submitted_at": now.strftime("%Y-%m-%d %H:%MZ"),
    "name": "Chris Example",
    "phone": "352-555-1212",
    "email": "chris@example.org",
    "staff_involved": "Jane Doe",
    "involves": {"grace_staff": True, "policies_procedures": False, "volunteer": True, "other_text": "Security"},
    "incident_date": "2025-09-08",
    "incident_time": "10:30",
    "description": (
        "This is a long multi-line description to test wrapping and positioning. "
        "We will nudge coordinates after the first print if needed."
    ),
}

render_grievance_pdf(data, TEMPLATE, pdf_path)
print("Wrote:", pdf_path)
