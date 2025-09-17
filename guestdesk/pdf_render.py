from __future__ import annotations
import io
import json
from typing import Dict, Any, Tuple

from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Frame
from reportlab.lib.styles import ParagraphStyle
from PyPDF2 import PdfReader, PdfWriter


def to_points_box(box: Dict[str, Any], W: float, H: float) -> Tuple[float, float, float, float]:
    x = float(box.get("x", 0.0)) * W
    w = float(box.get("w", 0.0)) * W
    y_top = float(box.get("y", 0.0))
    h_norm = float(box.get("h", 0.0))
    y_bl = (1.0 - (y_top + h_norm)) * H
    h = h_norm * H
    return x, y_bl, w, h


def to_points_checkbox(cb: Dict[str, Any], W: float, H: float) -> Tuple[float, float, float]:
    cx = float(cb.get("cx", 0.0)) * W
    cy = (1.0 - float(cb.get("cy", 0.0))) * H
    size = float(cb.get("size", 0.018)) * min(W, H)
    return cx, cy, size


def draw_line(c: canvas.Canvas, x: float, y: float, w: float, h: float, text: str, pad: float = 3.0, font: str = "Helvetica", size: int = 10):
    c.setFont(font or "Helvetica", size or 10)
    c.drawString(x, y + float(pad or 0), text or "")


def draw_multiline(c: canvas.Canvas, x: float, y: float, w: float, h: float, text: str, size: int = 10, leading: int = 12, align: str = "left", ellipsis: bool = True):
    style = ParagraphStyle(
        "f",
        fontName="Helvetica",
        fontSize=size or 10,
        leading=leading or 12,
        alignment={"left": 0, "center": 1, "right": 2}.get(align, 0),
    )
    p = Paragraph((text or "").replace("\n", "<br/>"), style)
    f = Frame(x, y, w, h, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, showBoundary=0)
    f.addFromList([p], c)


def draw_checkbox(c: canvas.Canvas, cx: float, cy: float, size: float, checked: bool = True):
    half = size / 2.0
    if checked:
        c.setLineWidth(1)
        c.line(cx - half, cy - half, cx + half, cy + half)
        c.line(cx - half, cy + half, cx + half, cy - half)


def _render_overlay_page(page_layout: Dict[str, Any], page_w: float, page_h: float, data: Dict[str, Any], baseline_pad_pt: float = 3.0, debug: bool = False) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))

    fields = (page_layout or {}).get("fields", {})
    for key, spec in fields.items():
        ftype = (spec.get("type") if isinstance(spec, dict) else None) or "line"
        text_val = str(data.get(key) or "")
        if ftype in ("line", "multiline"):
            x, y, w, h = to_points_box(spec, page_w, page_h)
            if debug:
                # faint boundary
                c.setLineWidth(0.3)
                c.setStrokeGray(0.8)
                c.rect(x, y, w, h, stroke=1, fill=0)
                # baseline indicator
                c.line(x, y + baseline_pad_pt, x + w, y + baseline_pad_pt)
                c.setStrokeGray(0.0)
            font = spec.get("font", "Helvetica")
            size = int(spec.get("size", 10))
            if ftype == "line":
                draw_line(c, x, y, w, h, text_val, pad=baseline_pad_pt, font=font, size=size)
            else:
                leading = int(spec.get("leading", max(size + 2, 12)))
                align = spec.get("align", "left")
                ellipsis = bool(spec.get("ellipsis", True))
                # For true ellipsis we'd need text measurement; best-effort via frame clip
                draw_multiline(c, x, y, w, h, text_val, size=size, leading=leading, align=align, ellipsis=ellipsis)
        elif ftype == "checkbox":
            cx, cy, size = to_points_checkbox(spec, page_w, page_h)
            draw_checkbox(c, cx, cy, size, checked=bool(data.get(key)))

    c.showPage()
    c.save()
    return buf.getvalue()


def render_pdf_v1(template_pdf_path: str, layout_json: Dict[str, Any], page_w: float, page_h: float, baseline_pad_pt: float, data: Dict[str, Any], strict_size: bool = True, debug: bool = False) -> bytes:
    reader = PdfReader(template_pdf_path)
    # Strict size check
    for i, pg in enumerate(reader.pages):
        mediabox = pg.mediabox
        w = float(mediabox.right - mediabox.left)
        h = float(mediabox.top - mediabox.bottom)
        if strict_size and (abs(w - page_w) > 0.1 or abs(h - page_h) > 0.1):
            raise ValueError(f"Template page {i+1} size mismatch: got {w}x{h}, expected {page_w}x{page_h}")

    writer = PdfWriter()
    pages_layout = (layout_json or {}).get("pages", [])
    num_pages = len(reader.pages)
    for i in range(num_pages):
        base_page = reader.pages[i]
        # choose layout page i or last available
        layout_page = pages_layout[i] if i < len(pages_layout) else (pages_layout[-1] if pages_layout else {"fields": {}})
        overlay_bytes = _render_overlay_page(layout_page, page_w, page_h, data, baseline_pad_pt=baseline_pad_pt, debug=debug)
        overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
        base_page.merge_page(overlay_reader.pages[0])
        writer.add_page(base_page)

    out_buf = io.BytesIO()
    writer.write(out_buf)
    return out_buf.getvalue()


# ---- New simplified renderer: bottom-left coordinates in points ----
def _draw_debug(c: canvas.Canvas, page_w: float, page_h: float):
    try:
        # Light grid each 12pt, bold each 72pt with labels
        c.setLineWidth(0.2)
        c.setStrokeGray(0.85)
        for x in range(0, int(page_w) + 1, 12):
            if x % 72 == 0:
                c.setLineWidth(0.6)
                c.setStrokeGray(0.6)
            else:
                c.setLineWidth(0.2)
                c.setStrokeGray(0.85)
            c.line(x, 0, x, page_h)
        for y in range(0, int(page_h) + 1, 12):
            if y % 72 == 0:
                c.setLineWidth(0.6)
                c.setStrokeGray(0.6)
            else:
                c.setLineWidth(0.2)
                c.setStrokeGray(0.85)
            c.line(0, y, page_w, y)
        c.setStrokeGray(0.0)
        c.setFont("Helvetica-Bold", 9)
        for x in range(0, int(page_w) + 1, 72):
            c.drawString(x + 2, page_h - 12, str(x))
        for y in range(0, int(page_h) + 1, 72):
            c.drawString(2, y + 2, str(y))
        # Proof mark
        c.setFont("Helvetica-Bold", 12)
        c.setStrokeGray(1.0)
        c.setFillGray(1.0)
        c.drawString(100, page_h - 72, "PROOF (100,700)")
        c.setStrokeGray(0.9)
        c.line(96, page_h - 76, 104, page_h - 68)
        c.line(96, page_h - 68, 104, page_h - 76)
    except Exception:
        pass


def render_pdf(template_path: str, layout_json: Any, data_dict: Dict[str, Any], pad: float = 3.0, debug: bool = False) -> bytes:
    """
    Render overlay according to simplified schema where layout_json is a mapping of
    field -> [x,y,w,h] for text, or [cx,cy] for checkboxes. Coordinates are bottom-left
    in points relative to the visible page (CropBox). Optional layout_json['pad'] overrides pad.
    """
    # Parse layout_json if string
    if isinstance(layout_json, str):
        try:
            layout = json.loads(layout_json or "{}")
        except Exception:
            layout = {}
    else:
        layout = layout_json or {}

    # Allow pad override from layout
    try:
        if 'pad' in layout and layout['pad'] is not None:
            pad = float(layout.get('pad') or pad)
    except Exception:
        pass

    reader = PdfReader(template_path)
    writer = PdfWriter()

    # Compute transform from CropBox to MediaBox
    def _page_geom(pg) -> Tuple[float, float, float, float, float, float]:
        mb = pg.mediabox
        cb = getattr(pg, 'cropbox', None) or mb
        mb_left, mb_bottom, mb_right, mb_top = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
        cb_left, cb_bottom, cb_right, cb_top = float(cb.left), float(cb.bottom), float(cb.right), float(cb.top)
        page_w = cb_right - cb_left
        page_h = cb_top - cb_bottom
        shift_x = cb_left - mb_left
        shift_y = cb_bottom - mb_bottom
        return page_w, page_h, shift_x, shift_y, mb_right - mb_left, mb_top - mb_bottom

    # Prepare overlay for each page using same layout
    for i in range(len(reader.pages)):
        base_page = reader.pages[i]
        page_w, page_h, shift_x, shift_y, media_w, media_h = _page_geom(base_page)

        buf = io.BytesIO()
        # Overlay canvas matches MediaBox size so merging aligns at (0,0)
        c = canvas.Canvas(buf, pagesize=(media_w, media_h))

        if debug:
            _draw_debug(c, media_w, media_h)

        # Draw fields from simplified layout
        for key, val in (layout or {}).items():
            if key == 'pad':
                continue
            v = data_dict.get(key)
            if isinstance(val, (list, tuple)) and len(val) == 2:
                # Checkbox
                cx = float(val[0]) + shift_x
                cy = float(val[1]) + shift_y
                if v:
                    half = 5  # ~10pt full size
                    c.setLineWidth(1)
                    c.line(cx - half, cy - half, cx + half, cy + half)
                    c.line(cx - half, cy + half, cx + half, cy - half)
            elif isinstance(val, (list, tuple)) and len(val) == 4:
                # Text / multiline box
                x = float(val[0]) + shift_x
                y = float(val[1]) + shift_y
                w = float(val[2])
                h = float(val[3])
                text_val = str(v or "")
                # Heuristic: multiline if height > ~18pt
                multiline = h > 18
                if debug:
                    c.setLineWidth(0.3)
                    c.setStrokeGray(0.8)
                    c.rect(x, y, w, h, stroke=1, fill=0)
                    c.line(x, y + float(pad or 0), x + w, y + float(pad or 0))
                    c.setStrokeGray(0.0)
                if multiline:
                    # top-left = (x, y + h), clip to h
                    style = ParagraphStyle(
                        "f",
                        fontName="Helvetica",
                        fontSize=10,
                        leading=12,
                        alignment=0,
                    )
                    p = Paragraph((text_val or "").replace("\n", "<br/>"), style)
                    f = Frame(x, y, w, h, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, showBoundary=0)
                    f.addFromList([p], c)
                else:
                    c.setFont("Helvetica", 10)
                    c.drawString(x, y + float(pad or 0), text_val)

        c.showPage()
        c.save()
        overlay_reader = PdfReader(io.BytesIO(buf.getvalue()))
        base_page.merge_page(overlay_reader.pages[0])
        writer.add_page(base_page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
