import io
import os
import textwrap
import datetime as dt
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from PyPDF2 import PdfReader, PdfWriter


def _draw_wrapped(c, text, x, y, width_chars=100, leading=12, font=("Helvetica", 11)):
    c.setFont(*font)
    for line in textwrap.wrap(text or "", width=width_chars):
        c.drawString(x, y, line)
        y -= leading
    return y


def generate_grv_id(now=None):
    now = now or dt.datetime.utcnow()
    # Stable, sortable ID; replace with DB autoincrement if you prefer
    return f"GRV-{now.strftime('%Y')}-{int(now.timestamp())}"


def render_grievance_pdf(data, template_path, out_path):
    """
    data = {
      "id": "GRV-2025-0001",
      "submitted_at": "2025-09-09 14:06Z",
      "name": "Guest Name",
      "phone": "352-555-1212",
      "email": "guest@example.org",
      "staff_involved": "Jane D.",
      "involves": {"grace_staff": True, "policies_procedures": False, "volunteer": True, "other_text": "Security"},
      "incident_date": "2025-09-08",
      "incident_time": "10:30",
      "description": "Long text…"
    }
    """
    # 1) overlay in memory
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=letter)

    # --- Initial coordinates (letter = 612x792, origin bottom-left) ---
    # Nudge coordinates after first test print if needed.

    # Header info (top-right)
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(595, 770, data.get("id", ""))
    c.setFont("Helvetica", 9)
    c.drawRightString(595, 756, f"Submitted: {data.get('submitted_at','')}")

    # Phone / Email row
    c.setFont("Helvetica", 11)
    c.drawString(85, 690, data.get("phone", ""))
    c.drawString(350, 690, data.get("email", ""))

    # Staff involved
    c.drawString(200, 660, data.get("staff_involved", ""))

    # Checkboxes row (mark with X)
    def chk(x, y, flag):
        if flag:
            c.drawString(x, y, "✗")

    involves = data.get("involves", {})
    chk(120, 632, involves.get("grace_staff"))
    chk(310, 632, involves.get("policies_procedures"))
    chk(430, 632, involves.get("volunteer"))
    chk(520, 632, bool(involves.get("other_text")))
    c.drawString(540, 632, involves.get("other_text", ""))

    # Incident date/time
    c.drawString(85, 604, data.get("incident_date", ""))
    c.drawString(300, 604, data.get("incident_time", ""))

    # Description (multi-line)
    _draw_wrapped(c, data.get("description", ""), x=40, y=560, width_chars=100, leading=12)

    c.save()
    packet.seek(0)

    # 2) Merge overlay with template
    template_reader = PdfReader(template_path)
    base_page = template_reader.pages[0]  # template is a single-page (page 6 extracted)

    overlay_reader = PdfReader(packet)
    overlay_page = overlay_reader.pages[0]
    base_page.merge_page(overlay_page)

    writer = PdfWriter()
    writer.add_page(base_page)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        writer.write(f)

    return out_path

