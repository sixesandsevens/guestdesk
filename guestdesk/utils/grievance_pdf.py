import io
import os
import json
import datetime as dt
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.pagesizes import letter
from PyPDF2 import PdfReader, PdfWriter


DEFAULT_BOX = {
    "name":               (40, 718, 260, 20),
    "phone":              (85, 690, 200, 16),
    "email":              (350, 690, 220, 16),
    "staff_involved":     (200, 660, 300, 16),
    "involves_staff":     (108, 628, 14, 14),
    "involves_policies":  (298, 628, 14, 14),
    "involves_volunteer": (418, 628, 14, 14),
    "involves_other_chk": (508, 628, 14, 14),
    "involves_other_txt": (540, 628, 60, 14),
    "incident_date":      (85, 604, 140, 16),
    "incident_time":      (300, 604, 120, 16),
    "description":        (40, 560, 535, 110),
    "id":                 (440, 765, 150, 12),
    "submitted":          (440, 750, 150, 12),
}

BOXES_PATH = os.getenv(
    "GRIEVANCE_BOXES_JSON",
    "/opt/guestdesk/guestdesk/utils/grievance_boxes.json",
)

# Baseline padding (points) for single-line fields
BASELINE_PAD = float(os.getenv("GRIEVANCE_BASELINE_PAD", "3"))


def load_boxes():
    path = BOXES_PATH
    try:
        with open(path, "r") as f:
            obj = json.load(f)
        return {k: tuple(obj[k]) for k in obj}
    except Exception:
        # Fallback to local file in package dir
        try:
            here = os.path.dirname(__file__)
            alt = os.path.join(here, "grievance_boxes.json")
            with open(alt, "r") as f:
                obj = json.load(f)
            return {k: tuple(obj[k]) for k in obj}
        except Exception:
            # Last resort: built-in defaults
            return DEFAULT_BOX


def _wrap_by_width(text: str, font: str, size: int, max_width: float) -> list[str]:
    words = (text or "").split()
    lines: list[str] = []
    line = ""
    for wd in words:
        test = (line + " " + wd).strip()
        if pdfmetrics.stringWidth(test, font, size) <= max_width or not line:
            line = test
        else:
            lines.append(line)
            line = wd
    if line:
        lines.append(line)
    return lines


def draw_in_box(c: canvas.Canvas, text: str, box, font="Helvetica", size=11, valign="middle", halign="left", leading: float | None = None, ellipsis: bool = False):
    x, y, w, h = box
    c.setFont(font, size)
    lines_all = _wrap_by_width(text or "", font, size, w)
    leading = leading or (size * 1.2)
    # Clip to available height
    max_lines = max(1, int(h // leading))
    lines = lines_all[:max_lines]
    # Optional ellipsis if truncated
    if ellipsis and len(lines_all) > max_lines and lines:
        last = lines[-1]
        dot = "…"
        # ensure it fits
        if pdfmetrics.stringWidth(last + dot, font, size) <= w:
            lines[-1] = last + dot
        else:
            # trim characters until it fits
            while last and pdfmetrics.stringWidth(last + dot, font, size) > w:
                last = last[:-1]
            lines[-1] = (last + dot) if last else dot
    total_h = leading * len(lines)
    if valign == "middle":
        yy = y + (h - total_h) / 2 + (len(lines) - 1) * leading
    elif valign == "top":
        yy = y + h - leading
    else:  # bottom
        yy = y
    for ln in lines:
        if halign == "center":
            tx = x + (w - pdfmetrics.stringWidth(ln, font, size)) / 2
        elif halign == "right":
            tx = x + w - pdfmetrics.stringWidth(ln, font, size)
        else:
            tx = x
        c.drawString(tx, yy, ln)
        yy -= leading


def draw_checkbox_x(c: canvas.Canvas, box, checked: bool):
    if not checked:
        return
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    c.setFont("Helvetica", h)
    # Slight vertical optical adjustment
    c.drawCentredString(cx, cy - h * 0.35, "✗")


def draw_in_box_bottom(c: canvas.Canvas, text: str, box, font: str = "Helvetica", size: int = 11, pad: float = BASELINE_PAD):
    """Draw single-line text bottom-aligned within box (for fields with printed baselines)."""
    x, y, w, h = box
    c.setFont(font, size)
    t = (text or "").strip()
    # Left-align; adjust if future forms require center/right
    c.drawString(x, y + pad, t)


def draw_box_guides(c: canvas.Canvas, BOX: dict):
    c.setDash(2, 2)
    for key, (x, y, w, h) in BOX.items():
        c.rect(x, y, w, h)
        c.setFont("Helvetica", 7)
        c.drawString(x, y + h + 2, key)
    c.setDash()


def draw_grid(c: canvas.Canvas, page_w: float, page_h: float, step: int = 36):
    """Draw light grid ticks every `step` points for calibration."""
    c.setStrokeColorRGB(0.8, 0.8, 0.8)
    c.setLineWidth(0.1)
    # vertical lines
    x = 0
    while x <= page_w:
        c.line(x, 0, x, page_h)
        if x % (step * 2) == 0:
            c.setFont("Helvetica", 6)
            c.drawString(x + 2, page_h - 8, str(int(x)))
        x += step
    # horizontal lines
    y = 0
    while y <= page_h:
        c.line(0, y, page_w, y)
        if y % (step * 2) == 0:
            c.setFont("Helvetica", 6)
            c.drawString(2, y + 2, str(int(y)))
        y += step
    # reset
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(1)


def draw_checkbox_centers(c: canvas.Canvas, BOX: dict, keys: list[str]):
    c.setStrokeColorRGB(0.2, 0.4, 1)
    for k in keys:
        if k in BOX:
            x, y, w, h = BOX[k]
            cx, cy = x + w / 2, y + h / 2
            c.line(cx - 3, cy, cx + 3, cy)
            c.line(cx, cy - 3, cx, cy + 3)
    c.setStrokeColorRGB(0, 0, 0)


def generate_grv_id(now=None):
    now = now or dt.datetime.utcnow()
    # Stable, sortable ID; replace with DB autoincrement if you prefer
    return f"GRV-{now.strftime('%Y')}-{int(now.timestamp())}"


def render_grievance_pdf(data, template_path, out_path):
    # Try reading template to match its exact page size; fallback to Letter if missing
    base_page = None
    W, H = letter
    try:
        template_reader = PdfReader(template_path)
        base_page = template_reader.pages[0]
        mb = getattr(base_page, 'mediabox', None) or getattr(base_page, 'MediaBox', None)
        try:
            W = float(mb.width)
            H = float(mb.height)
        except Exception:
            # Fallback for tuple-like mediabox
            W = float(mb[2]) - float(mb[0])
            H = float(mb[3]) - float(mb[1])
    except Exception:
        # No template available; continue with a blank page overlay at Letter size
        base_page = None

    # 1) overlay in memory with template's size
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(W, H))

    # Load boxes and optional calibration overlays
    BOX = load_boxes()
    dbg = os.getenv("GRIEVANCE_DEBUG_PDF", os.getenv("DEBUG_PDF", "0"))
    if str(dbg).lower() in ("1", "true", "yes", "on"):
        draw_box_guides(c, BOX)
        draw_grid(c, W, H, step=36)
        draw_checkbox_centers(c, BOX, [
            "involves_staff", "involves_policies", "involves_volunteer", "involves_other_chk",
        ])
        # Stamp debug info at bottom-left (helps confirm live values)
        try:
            c.setFont("Helvetica", 7)
            debug_lines = [
                f"DBG {dt.datetime.utcnow():%Y-%m-%d %H:%M:%SZ}",
                f"BOXES_PATH={BOXES_PATH}",
                f"name={BOX.get('name')}",
                f"phone={BOX.get('phone')}",
                f"email={BOX.get('email')}",
                f"id={BOX.get('id')}",
            ]
            yy = 22
            for line in debug_lines:
                c.drawString(40, yy, line)
                yy += 9
        except Exception:
            pass

    # Header
    draw_in_box(c, data.get("id", ""), BOX["id"], font="Helvetica-Bold", size=10, halign="right", valign="middle")
    draw_in_box(c, f"Submitted: {data.get('submitted_at','')}", BOX["submitted"], font="Helvetica", size=9, halign="right", valign="middle")

    # Fields (single-line on printed baselines: bottom align with small pad)
    draw_in_box_bottom(c, data.get("name", ""), BOX["name"], size=11, pad=3)
    draw_in_box_bottom(c, data.get("phone", ""), BOX["phone"], size=11, pad=3)
    draw_in_box_bottom(c, data.get("email", ""), BOX["email"], size=11, pad=3)
    draw_in_box_bottom(c, data.get("staff_involved", ""), BOX["staff_involved"], size=11, pad=3)

    inv = data.get("involves", {}) or {}
    draw_checkbox_x(c, BOX["involves_staff"], bool(inv.get("grace_staff")))
    draw_checkbox_x(c, BOX["involves_policies"], bool(inv.get("policies_procedures")))
    draw_checkbox_x(c, BOX["involves_volunteer"], bool(inv.get("volunteer")))
    draw_checkbox_x(c, BOX["involves_other_chk"], bool(inv.get("other_text")))
    draw_in_box(c, inv.get("other_text", ""), BOX["involves_other_txt"], size=10, valign="middle")

    draw_in_box_bottom(c, data.get("incident_date", ""), BOX["incident_date"], size=11, pad=3)
    draw_in_box_bottom(c, data.get("incident_time", ""), BOX["incident_time"], size=11, pad=3)

    # Description as paragraph (top-aligned)
    draw_in_box(c, data.get("description", ""), BOX["description"], size=11, valign="top", halign="left", leading=13, ellipsis=True)


def nudge_box(BOX: dict, key: str, dx: float = 0, dy: float = 0, dw: float = 0, dh: float = 0):
    """Temporarily adjust a box in-memory for testing; write final values to JSON when satisfied."""
    x, y, w, h = BOX[key]
    BOX[key] = (x + dx, y + dy, w + dw, h + dh)

    c.save()
    packet.seek(0)

    # 2) Merge overlay with template if present; otherwise, use overlay alone

    overlay_reader = PdfReader(packet)
    overlay_page = overlay_reader.pages[0]

    writer = PdfWriter()
    if base_page is not None:
        try:
            base_page.merge_page(overlay_page)
            writer.add_page(base_page)
        except Exception:
            # If merge fails for any reason, fall back to overlay only
            writer.add_page(overlay_page)
    else:
        writer.add_page(overlay_page)

    # Write to memory first (for email attachment), then persist to disk
    mem = io.BytesIO()
    writer.write(mem)
    data_bytes = mem.getvalue()
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(data_bytes)
    except Exception:
        # Best effort: return bytes even if disk write fails
        pass

    return out_path, data_bytes
