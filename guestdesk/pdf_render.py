from __future__ import annotations
import io
import json
from typing import Dict, Any, Tuple

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
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


def render_pdf(template_pdf_path: str, layout_json: Dict[str, Any], page_w: float, page_h: float, baseline_pad_pt: float, data: Dict[str, Any], strict_size: bool = True, debug: bool = False) -> bytes:
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

