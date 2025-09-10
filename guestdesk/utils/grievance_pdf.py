import io
import os
import json
import datetime as dt
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.pagesizes import letter
from PyPDF2 import PdfReader, PdfWriter, Transformation
from PyPDF2.errors import PdfReadError


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

# Config paths and toggles
BOXES_PATH = os.getenv(
    "GRIEVANCE_BOXES_JSON",
    "/opt/guestdesk/guestdesk/utils/grievance_boxes.json",
)

# Baseline padding (points) for single-line fields (support alias env)
BASELINE_PAD = float(os.getenv("GRV_BASELINE_PAD", os.getenv("GRIEVANCE_BASELINE_PAD", "3")))

# Debug flag
DEBUG_PDF = (os.getenv("GRIEVANCE_DEBUG_PDF", os.getenv("DEBUG_PDF", "0")) in ("1", "true", "True"))

# Global overlay shift (applied to all boxes)
GLOBAL_DX = float(os.getenv("GRV_GLOBAL_DX", "0") or 0)
GLOBAL_DY = float(os.getenv("GRV_GLOBAL_DY", "0") or 0)

# CropBox translation sign: 1 to add (default), -1 to subtract
CROP_SIGN = int(os.getenv("GRV_CROP_SIGN", "1") or 1)


def box_add(b, dx: float, dy: float):
    x, y, w, h = b
    return (x + dx, y + dy, w, h)


def boxes_with_global_offset(BOX: dict) -> dict:
    if not (GLOBAL_DX or GLOBAL_DY):
        return BOX
    return {k: box_add(v, GLOBAL_DX, GLOBAL_DY) for k, v in BOX.items()}


def clamp_box(b, max_w: float, max_h: float):
    """Clamp a box so it stays on-page after global shifts.

    Prevents accidental off-page coordinates during coarse alignment.
    """
    x, y, w, h = b
    try:
        x = max(0, min(x, max_w - w))
        y = max(0, min(y, max_h - h))
    except Exception:
        pass
    return (x, y, w, h)


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
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    if DEBUG_PDF:
        try:
            c.setDash(1, 1)
            c.line(cx - 2, cy, cx + 2, cy)
            c.line(cx, cy - 2, cx, cy + 2)
            c.setDash()
        except Exception:
            pass
    if not checked:
        return
    c.setFont("Helvetica", h)
    # Slight vertical optical adjustment
    c.drawCentredString(cx, cy - h * 0.32, "✗")


def draw_in_box_bottom(c: canvas.Canvas, text: str, box, font: str = "Helvetica", size: int = 11, pad: float = BASELINE_PAD, dbg_label: str | None = None):
    """Draw single-line text bottom-aligned within box (for fields with printed baselines).

    When DEBUG_PDF is true and dbg_label provided, draw a small hint like
    "[name:bottom]" near the baseline to confirm the bottom drawer is used.
    """
    x, y, w, h = box
    c.setFont(font, size)
    t = (text or "").strip()
    # Left-align; adjust if future forms require center/right
    c.drawString(x, y + pad, t)
    if DEBUG_PDF and dbg_label:
        try:
            c.setFont("Helvetica", 6)
            c.drawString(x + w + 3, y + pad, f"[{dbg_label}:bottom]")
        except Exception:
            pass

def draw_paragraph_in_box(c: canvas.Canvas, text: str, box, font: str = "Helvetica", size: int = 11, leading: float = 13):
    """Top-aligned paragraph with width-based wrapping and ellipsis when clipped."""
    x, y, w, h = box
    c.setFont(font, size)
    words = (text or "").split()
    lines: list[str] = []
    line = ""
    for wd in words:
        test = (line + " " + wd).strip()
        if pdfmetrics.stringWidth(test, font, size) <= w:
            line = test
        else:
            lines.append(line)
            line = wd
    if line:
        lines.append(line)
    max_lines = max(1, int(h // leading))
    clipped = len(lines) > max_lines
    lines = lines[:max_lines]
    if clipped:
        last = lines[-1]
        while last and pdfmetrics.stringWidth(last + "…", font, size) > w:
            last = last[:-1]
        lines[-1] = (last + "…") if last else "…"
    yy = y + h - leading
    for ln in lines:
        c.drawString(x, yy, ln)
        yy -= leading


def draw_box_guides(c: canvas.Canvas, BOX: dict):
    c.setDash(2, 2)
    for key, (x, y, w, h) in BOX.items():
        c.rect(x, y, w, h)
        c.setFont("Helvetica", 7)
        c.drawString(x, y + h + 2, key)
    c.setDash()


def draw_grid(c: canvas.Canvas, page_w: float, page_h: float, step: int = 36):
    """Draw grid ticks; light every 36pt, bold labeled every 72pt."""
    # Fine grid (36pt)
    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.setLineWidth(0.2)
    x = 0
    while x <= page_w:
        c.line(x, 0, x, page_h)
        x += step
    y = 0
    while y <= page_h:
        c.line(0, y, page_w, y)
        y += step
    # Inch grid (72pt) with labels
    c.setStrokeColorRGB(0.8, 0.0, 0.0)
    c.setLineWidth(0.5)
    c.setFont("Helvetica", 6)
    x = 0
    while x <= page_w:
        c.line(x, 0, x, page_h)
        if x > 0:
            c.drawString(x + 2, page_h - 10, str(int(x)))
        x += 72
    y = 0
    while y <= page_h:
        c.line(0, y, page_w, y)
        if y > 0:
            c.drawString(8, y + 2, str(int(y)))
        y += 72
    # reset pen
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(1)


def draw_precision_grid(c: canvas.Canvas, w: float, h: float, show_targets: bool = False, targets: dict | None = None):
    """Precision grid for DEBUG reading.

    - minor @12pt, mid @36pt, major @72pt (labeled)
    - axis numbers every 24pt on left/bottom with small gutters
    - optional target baselines from a dict {field: y}
    """
    gutter_w = 28
    gutter_h = 20

    # Background gutters to improve label readability
    try:
        c.setFillColorRGB(1, 1, 1)
        c.setStrokeColorRGB(1, 1, 1)
        c.setLineWidth(0)
        c.rect(0, 0, w, gutter_h, fill=1, stroke=0)
        c.rect(0, 0, gutter_w, h, fill=1, stroke=0)
    except Exception:
        pass

    # Minor grid (12pt)
    c.setStrokeColorRGB(0.90, 0.90, 0.90)
    c.setLineWidth(0.15)
    for y in range(0, int(h) + 1, 12):
        c.line(0, y, w, y)
    for x in range(0, int(w) + 1, 12):
        c.line(x, 0, x, h)

    # Mid grid (36pt)
    c.setStrokeColorRGB(0.82, 0.82, 0.82)
    c.setLineWidth(0.25)
    for y in range(0, int(h) + 1, 36):
        c.line(0, y, w, y)
    for x in range(0, int(w) + 1, 36):
        c.line(x, 0, x, h)

    # Major grid (72pt)
    c.setStrokeColorRGB(0.80, 0.15, 0.15)
    c.setLineWidth(0.5)
    for y in range(0, int(h) + 1, 72):
        c.line(0, y, w, y)
    for x in range(0, int(w) + 1, 72):
        c.line(x, 0, x, h)

    # Axis numbers every 24pt
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.15, 0.15, 0.15)
    c.setStrokeColorRGB(0.3, 0.3, 0.3)
    for y in range(0, int(h) + 1, 24):
        c.drawRightString(gutter_w - 2, y + 2, str(int(y)))
        c.setLineWidth(0.25)
        c.line(gutter_w - 6, y, gutter_w, y)
    for x in range(0, int(w) + 1, 24):
        c.drawString(x + 2, 2, str(int(x)))
        c.setLineWidth(0.25)
        c.line(x, gutter_h, x, gutter_h + 4)

    # Optional targets overlay
    if show_targets and targets:
        try:
            c.setStrokeColorRGB(0.20, 0.55, 0.90)
            c.setFillColorRGB(0.20, 0.55, 0.90)
            c.setLineWidth(0.6)
            c.setFont("Helvetica", 7)
            for key, val in targets.items():
                try:
                    y = float(val)
                except Exception:
                    continue
                c.line(0, y, w, y)
                c.drawString(4, y + 8, f"{key}:{int(y)}")
        except Exception:
            pass

    # Restore defaults for subsequent drawing
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(1.0)


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
    """Render a grievance PDF using a required template.

    Returns (out_path, bytes). Raises RuntimeError on any template/merge error.
    """
    if not template_path or not os.path.exists(template_path):
        raise RuntimeError(f"Template missing: {template_path}")

    # Read template and determine exact page size + rotation
    try:
        template_reader = PdfReader(template_path)
        base_page = template_reader.pages[0]
        # Geometry: Mediabox size, rotation, and CropBox lower-left offset
        try:
            W = float(base_page.mediabox.width)
            H = float(base_page.mediabox.height)
        except Exception:
            mb = getattr(base_page, "mediabox", None) or getattr(base_page, "MediaBox", None)
            W = float(mb[2]) - float(mb[0])
            H = float(mb[3]) - float(mb[1])
        try:
            rotation = int((base_page.get("/Rotate") or 0)) % 360
        except Exception:
            rotation = 0
        try:
            cllx, clly = base_page.cropbox.lower_left
            crop_llx, crop_lly = float(cllx), float(clly)
        except Exception:
            crop_llx, crop_lly = 0.0, 0.0
    except (PdfReadError, Exception) as e:
        raise RuntimeError(f"Failed to read template: {e}") from e

    # 1) overlay in memory with template's size
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(W, H))

    # Load boxes and optional calibration overlays
    BOX0 = load_boxes()
    BOX = boxes_with_global_offset(BOX0)
    # Safety: clamp to page after global shift (helps avoid off-page in DEBUG passes)
    BOX = {k: clamp_box(v, W, H) for k, v in BOX.items()}
    if DEBUG_PDF:
        # High-precision grid first (optional targets)
        _targets = None
        try:
            if os.getenv("GRV_SHOW_TARGETS", "0") in ("1", "true", "True"):
                with open("/opt/guestdesk/guestdesk/utils/grievance_targets.json", "r") as _f:
                    _targets = json.load(_f)
        except Exception:
            _targets = None
        draw_precision_grid(c, W, H, show_targets=bool(_targets), targets=_targets)
        # Legacy guides and 36/72 grid
        draw_box_guides(c, BOX)
        draw_grid(c, W, H, step=36)
        draw_checkbox_centers(c, BOX, [
            "involves_staff", "involves_policies", "involves_volunteer", "involves_other_chk",
        ])
        # Stamp debug info at bottom-left (helps confirm live values)
        try:
            c.setFont("Helvetica", 7)
            # Gather extended page geometry diagnostics
            def _fmt_box(b):
                try:
                    ll = getattr(b, 'lower_left', None) or getattr(b, 'lowerLeft', None)
                    ur = getattr(b, 'upper_right', None) or getattr(b, 'upperRight', None)
                    llx = float(ll[0]); lly = float(ll[1])
                    urx = float(ur[0]); ury = float(ur[1])
                    return f"LL=({llx:.2f},{lly:.2f}) UR=({urx:.2f},{ury:.2f})"
                except Exception:
                    return "(n/a)"
            try:
                mbox_info = _fmt_box(base_page.mediabox)
            except Exception:
                mbox_info = "(n/a)"
            try:
                cbox_info = _fmt_box(base_page.cropbox)
            except Exception:
                cbox_info = "(n/a)"
            try:
                tbox_info = _fmt_box(getattr(base_page, 'trimbox'))
            except Exception:
                tbox_info = "(n/a)"
            try:
                abox_info = _fmt_box(getattr(base_page, 'artbox'))
            except Exception:
                abox_info = "(n/a)"
            try:
                bbox_info = _fmt_box(getattr(base_page, 'bleedbox'))
            except Exception:
                bbox_info = "(n/a)"
            debug_lines = [
                f"DBG {dt.datetime.utcnow():%Y-%m-%d %H:%M:%SZ}",
                f"BOXES_PATH={BOXES_PATH}",
                f"TPL_SIZE={W}x{H} ROT={rotation}",
                f"CROP_LL=({crop_llx},{crop_lly})",
                f"GLOBAL_DX={GLOBAL_DX} GLOBAL_DY={GLOBAL_DY} BASE_PAD={BASELINE_PAD}",
                f"CROP_SIGN={CROP_SIGN}",
                f"Mediabox {mbox_info}",
                f"CropBox  {cbox_info}",
                f"TrimBox  {tbox_info}",
                f"ArtBox   {abox_info}",
                f"BleedBox {bbox_info}",
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
        # Draw PROOF box at an easy-to-spot location
        try:
            c.setStrokeColorRGB(1, 0, 0)
            c.setLineWidth(1.2)
            c.rect(100, 700, 200, 24)
            cx, cy = 100 + 100, 700 + 12
            c.line(cx - 6, cy, cx + 6, cy)
            c.line(cx, cy - 6, cx, cy + 6)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(105, 704, "PROOF (100,700)")
            c.setStrokeColorRGB(0, 0, 0)
        except Exception:
            pass

    # Header
    draw_in_box(c, data.get("id", ""), BOX["id"], font="Helvetica-Bold", size=10, halign="right", valign="middle")
    draw_in_box(c, f"Submitted: {data.get('submitted_at','')}", BOX["submitted"], font="Helvetica", size=9, halign="right", valign="middle")

    # Fields (single-line on printed baselines: bottom align with debug hints)
    draw_in_box_bottom(c, data.get("name", ""),           BOX["name"],           size=11, pad=BASELINE_PAD, dbg_label="name")
    draw_in_box_bottom(c, data.get("phone", ""),          BOX["phone"],          size=11, pad=BASELINE_PAD, dbg_label="phone")
    draw_in_box_bottom(c, data.get("email", ""),          BOX["email"],          size=11, pad=BASELINE_PAD, dbg_label="email")
    draw_in_box_bottom(c, data.get("staff_involved", ""), BOX["staff_involved"], size=11, pad=BASELINE_PAD, dbg_label="staff")

    inv = data.get("involves", {}) or {}
    draw_checkbox_x(c, BOX["involves_staff"], bool(inv.get("grace_staff")))
    draw_checkbox_x(c, BOX["involves_policies"], bool(inv.get("policies_procedures")))
    draw_checkbox_x(c, BOX["involves_volunteer"], bool(inv.get("volunteer")))
    other_checked = bool(inv.get("other_checked")) or bool(inv.get("other_text"))
    draw_checkbox_x(c, BOX["involves_other_chk"], other_checked)
    draw_in_box(c, inv.get("other_text", ""), BOX["involves_other_txt"], size=10, valign="middle")

    draw_in_box_bottom(c, data.get("incident_date", ""), BOX["incident_date"], size=11, pad=BASELINE_PAD, dbg_label="date")
    draw_in_box_bottom(c, data.get("incident_time", ""), BOX["incident_time"], size=11, pad=BASELINE_PAD, dbg_label="time")

    # Description as paragraph (top-aligned with soft ellipsis)
    draw_paragraph_in_box(c, data.get("description", ""), BOX["description"], size=11, leading=13)

    # Finalize overlay
    c.save()
    packet.seek(0)

    # 2) Merge overlay with template; raise if merge fails
    try:
        overlay_reader = PdfReader(packet)
        overlay_page = overlay_reader.pages[0]
    except Exception as e:
        raise RuntimeError(f"Failed to read overlay: {e}") from e

    writer = PdfWriter()
    try:
        # Build transformation: rotate/translate for rotation + translate by CropBox lower-left
        t = None
        if rotation in (90, 180, 270):
            if rotation == 90:
                t = Transformation().rotate(90).translate(H, 0)
            elif rotation == 180:
                t = Transformation().rotate(180).translate(W, H)
            else:  # 270
                t = Transformation().rotate(270).translate(0, W)
        else:
            t = Transformation()
        # Apply cropbox lower-left translation last (in base page coords)
        if crop_llx or crop_lly:
            t = t.translate(CROP_SIGN * crop_llx, CROP_SIGN * crop_lly)
        # Merge with transform (even identity is fine)
        try:
            base_page.merge_transformed_page(overlay_page, t)
        except AttributeError:
            # Older PyPDF2 (camelCase API)
            try:
                base_page.mergeTransformedPage(overlay_page, t.ctm)
            except Exception:
                # As a last resort, attempt simple merge without transform
                base_page.merge_page(overlay_page)
        writer.add_page(base_page)
    except Exception as e:
        raise RuntimeError(f"Template merge failed: {e}") from e

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


def nudge_box(BOX: dict, key: str, dx: float = 0, dy: float = 0, dw: float = 0, dh: float = 0):
    """Temporarily adjust a box in-memory for testing; write final values to JSON when satisfied."""
    x, y, w, h = BOX[key]
    BOX[key] = (x + dx, y + dy, w + dw, h + dh)
