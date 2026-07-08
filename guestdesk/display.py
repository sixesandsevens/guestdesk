import json
import os
import re
from pathlib import Path
from functools import wraps

from flask import (
    Blueprint, render_template, jsonify,
    request, redirect, url_for, flash, abort,
    session, g, send_from_directory
)
from werkzeug.utils import secure_filename

from .permissions import permission_required_rw

bp = Blueprint("display", __name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_ROOT = Path(
    os.environ.get("GUESTDESK_DISPLAY_DIR")
    or os.environ.get("GUESTDESK_DATA_DIR")
    or os.environ.get("GUESTD_DATA_DIR")
    or "/var/lib/guestdesk"
) / "display"
DATA_PATH = DATA_ROOT / "display_config.json"
SLIDES_DIR = DATA_ROOT / "display_slides"
LEGACY_DATA_PATH = BASE_DIR / "data" / "display_config.json"
LEGACY_SLIDES_DIR = BASE_DIR / "static" / "display_slides"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTENSIONS = {".mp4"}
ALLOWED_TRANSITIONS = {"fade"}


def ensure_slide_storage():
    SLIDES_DIR.mkdir(parents=True, exist_ok=True)


def unique_filename(directory: Path, filename: str) -> str:
    candidate = filename
    base = Path(filename).stem or "slide"
    ext = Path(filename).suffix
    counter = 1
    while (directory / candidate).exists():
        candidate = f"{base}_{counter}{ext}"
        counter += 1
    return candidate


def clean_transition(_raw: str | None = None) -> str:
    return "fade"


def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def next_slide_id(slides: list[dict]) -> int:
    return (max(s["id"] for s in slides) + 1) if slides else 1


# ---------- Config load / save / migrate ----------

def _migrate_legacy(data: dict) -> dict:
    """
    Convert old {zones, slides} shape to new {displays, slideshows} shape.
    Each zone becomes a display + a slideshow named "<zone name>".
    Existing zone slides are moved into that slideshow.
    Called once on first load of old data; result is saved back.
    """
    zones = data.get("zones", [])
    flat_slides = data.get("slides", [])
    displays = {}
    slideshows = {}

    for zone in zones:
        slug = zone["slug"]
        ss_slug = slug  # slideshow gets same slug as old zone
        zone_slides = sorted(
            [s for s in flat_slides if s.get("zone_id") == zone["id"]],
            key=lambda s: s.get("order", s["id"]),
        )
        new_slides = []
        for s in zone_slides:
            ns = {
                "id": s["id"],
                "type": s.get("type", "text"),
                "duration": s.get("duration", 10),
                "active": s.get("active", True),
                "order": s.get("order", s["id"]),
                "transition": s.get("transition", "fade"),
            }
            if s.get("type") == "text":
                ns["headline"] = s.get("headline", "")
                ns["subheadline"] = s.get("subheadline", "")
                ns["body"] = s.get("body", "")
            elif s.get("type") in ("image", "video"):
                ns["file"] = s.get("file", "")
            new_slides.append(ns)

        slideshows[ss_slug] = {
            "name": zone.get("name", slug),
            "description": f"Migrated from display {zone.get('name', slug)}",
            "fade_duration": zone.get("fade_duration", 1.4),
            "slides": new_slides,
        }
        displays[slug] = {
            "name": zone.get("name", slug),
            "location": zone.get("location", ""),
            "active": zone.get("active", True),
            "assigned_slideshow": ss_slug,
        }

    return {"displays": displays, "slideshows": slideshows}


def load_config() -> dict:
    raw = None
    for path in (DATA_PATH, LEGACY_DATA_PATH):
        if path.exists():
            with path.open() as f:
                raw = json.load(f)
            break

    if not raw:
        return {"displays": {}, "slideshows": {}}

    # Migrate old zone-based format
    if "zones" in raw or "slides" in raw:
        raw = _migrate_legacy(raw)
        save_config(raw)
        return raw

    raw.setdefault("displays", {})
    raw.setdefault("slideshows", {})
    return raw


def save_config(cfg: dict):
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DATA_PATH.open("w") as f:
        json.dump(cfg, f, indent=2)


def get_display(cfg: dict, slug: str) -> dict | None:
    return cfg["displays"].get(slug)


def get_slideshow(cfg: dict, slug: str) -> dict | None:
    return cfg["slideshows"].get(slug)


def active_slides(slideshow: dict) -> list[dict]:
    slides = [s for s in slideshow.get("slides", []) if s.get("active", True)]
    slides.sort(key=lambda s: s.get("order", s["id"]))
    return slides


def normalize_orders(slides: list[dict]):
    for idx, slide in enumerate(slides, start=1):
        slide["order"] = idx


# ---------- Public API ----------

@bp.route("/api/display-slides/<display_slug>")
def display_slides_api(display_slug):
    """JSON API consumed by the TV display page."""
    cfg = load_config()
    display = get_display(cfg, display_slug)
    if not display or not display.get("active", True):
        abort(404)

    ss_slug = display.get("assigned_slideshow")
    slideshow = get_slideshow(cfg, ss_slug) if ss_slug else None
    if not slideshow:
        return jsonify({"display": display, "slideshow": None, "slides": []})

    slides = active_slides(slideshow)
    enriched = []
    for slide in slides:
        payload = dict(slide)
        payload["transition"] = slide.get("transition") or "fade"
        if slide.get("file") and slide.get("type") in ("image", "video"):
            payload["file_url"] = url_for("display.display_media", filename=slide["file"])
        enriched.append(payload)

    return jsonify({
        "display": display,
        "slideshow": {k: v for k, v in slideshow.items() if k != "slides"},
        "slides": enriched,
    })


def _render_display(display_slug: str):
    cfg = load_config()
    display = get_display(cfg, display_slug)
    if not display or not display.get("active", True):
        abort(404)
    preview = request.args.get("preview", "").lower() in {"1", "true", "yes", "on"}
    preview_duration = None
    if preview:
        try:
            preview_duration = float(request.args.get("duration", "2.5"))
        except (TypeError, ValueError):
            preview_duration = 2.5
        preview_duration = max(0.5, preview_duration)

    ss_slug = display.get("assigned_slideshow")
    slideshow = get_slideshow(cfg, ss_slug) if ss_slug else None
    fade = (slideshow or {}).get("fade_duration", 1.4)

    return render_template(
        "display_screen.html",
        display=display,
        display_slug=display_slug,
        slideshow_slug=None,
        slideshow=slideshow,
        fade_duration=fade,
        preview=preview,
        preview_duration=preview_duration,
    )


@bp.route("/api/slideshow-slides/<ss_slug>")
def slideshow_slides_api(ss_slug):
    """JSON API for slideshow preview — bypasses display lookup."""
    cfg = load_config()
    slideshow = get_slideshow(cfg, ss_slug)
    if not slideshow:
        abort(404)
    slides = active_slides(slideshow)
    enriched = []
    for slide in slides:
        payload = dict(slide)
        payload["transition"] = slide.get("transition") or "fade"
        if slide.get("file") and slide.get("type") in ("image", "video"):
            payload["file_url"] = url_for("display.display_media", filename=slide["file"])
        enriched.append(payload)
    return jsonify({
        "display": {"name": slideshow["name"], "active": True},
        "slideshow": {k: v for k, v in slideshow.items() if k != "slides"},
        "slides": enriched,
    })


@bp.route("/displays/<display_slug>")
def public_display(display_slug):
    """Permanent URL for Pi endpoints. Example: /displays/lobby-2"""
    return _render_display(display_slug)


@bp.route("/display/<slug>")
def display_screen(slug):
    """Legacy URL — kept so existing Pi bookmarks don't break."""
    return _render_display(slug)


@bp.route("/display-media/<path:filename>")
def display_media(filename):
    for directory in (SLIDES_DIR, LEGACY_SLIDES_DIR):
        candidate = directory / filename
        if candidate.exists():
            return send_from_directory(directory, filename)
    abort(404)


@bp.route("/slideshows/<ss_slug>/preview")
def slideshow_preview(ss_slug):
    cfg = load_config()
    slideshow = get_slideshow(cfg, ss_slug)
    if not slideshow:
        abort(404)
    try:
        preview_duration = float(request.args.get("duration", "2.5"))
    except (TypeError, ValueError):
        preview_duration = 2.5
    preview_duration = max(0.5, preview_duration)
    return render_template(
        "display_screen.html",
        display={"name": slideshow["name"], "active": True},
        display_slug=None,
        slideshow_slug=ss_slug,
        slideshow=slideshow,
        fade_duration=slideshow.get("fade_duration", 1.4),
        preview=True,
        preview_duration=preview_duration,
    )


# ---------- Admin: Displays ----------

@bp.route("/admin/displays", methods=["GET", "POST"])
@permission_required_rw('displays.view', 'displays.edit')
def admin_displays():
    cfg = load_config()
    displays = cfg["displays"]
    slideshows = cfg["slideshows"]

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_display":
            name = request.form.get("name", "").strip()
            slug = request.form.get("slug", "").strip()
            if not name or not slug:
                flash("Name and slug are required.", "danger")
            elif slug in displays:
                flash("Slug already in use.", "danger")
            else:
                displays[slug] = {
                    "name": name,
                    "location": request.form.get("location", "").strip(),
                    "active": True,
                    "assigned_slideshow": request.form.get("assigned_slideshow") or None,
                }
                save_config(cfg)
                flash("Display added.", "success")
            return redirect(url_for("display.admin_displays"))

        if action == "edit_display":
            slug = request.form.get("slug", "").strip()
            display = displays.get(slug)
            if not display:
                flash("Display not found.", "danger")
                return redirect(url_for("display.admin_displays"))
            new_slug = request.form.get("new_slug", "").strip() or slug
            new_name = request.form.get("name", "").strip()
            if not new_name:
                flash("Name is required.", "danger")
                return redirect(url_for("display.admin_displays"))
            if new_slug != slug and new_slug in displays:
                flash("Slug already in use.", "danger")
                return redirect(url_for("display.admin_displays"))
            display["name"] = new_name
            display["location"] = request.form.get("location", "").strip()
            display["assigned_slideshow"] = request.form.get("assigned_slideshow") or None
            if new_slug != slug:
                displays[new_slug] = display
                del displays[slug]
            save_config(cfg)
            flash("Display updated.", "success")
            return redirect(url_for("display.admin_displays"))

        if action == "toggle_display":
            slug = request.form.get("slug", "").strip()
            display = displays.get(slug)
            if display:
                display["active"] = not display.get("active", True)
                save_config(cfg)
                flash("Display updated.", "success")
            return redirect(url_for("display.admin_displays"))

        if action == "delete_display":
            slug = request.form.get("slug", "").strip()
            if slug in displays:
                del displays[slug]
                save_config(cfg)
                flash("Display deleted.", "success")
            return redirect(url_for("display.admin_displays"))

        if action == "assign_slideshow":
            slug = request.form.get("slug", "").strip()
            display = displays.get(slug)
            if display:
                display["assigned_slideshow"] = request.form.get("assigned_slideshow") or None
                save_config(cfg)
                flash("Slideshow assigned.", "success")
            return redirect(url_for("display.admin_displays"))

    return render_template(
        "admin/displays.html",
        displays=displays,
        slideshows=slideshows,
    )


# ---------- Admin: Slideshows ----------

@bp.route("/admin/slideshows", methods=["GET", "POST"])
@permission_required_rw('displays.view', 'displays.edit')
def admin_slideshows():
    cfg = load_config()
    slideshows = cfg["slideshows"]

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_slideshow":
            name = request.form.get("name", "").strip()
            slug = request.form.get("slug", "").strip() or slugify(name)
            if not name or not slug:
                flash("Name is required.", "danger")
            elif slug in slideshows:
                flash("Slug already in use.", "danger")
            else:
                try:
                    fade = float(request.form.get("fade_duration", "1.4"))
                except (TypeError, ValueError):
                    fade = 1.4
                slideshows[slug] = {
                    "name": name,
                    "description": request.form.get("description", "").strip(),
                    "fade_duration": max(0.1, fade),
                    "slides": [],
                }
                save_config(cfg)
                flash("Slideshow created.", "success")
                return redirect(url_for("display.admin_slideshow_edit", ss_slug=slug))
            return redirect(url_for("display.admin_slideshows"))

        if action == "delete_slideshow":
            slug = request.form.get("slug", "").strip()
            if slug in slideshows:
                # Check if any display is using it
                in_use = [
                    d["name"] for d in cfg["displays"].values()
                    if d.get("assigned_slideshow") == slug
                ]
                if in_use:
                    flash(
                        f"Cannot delete — assigned to: {', '.join(in_use)}.",
                        "danger",
                    )
                else:
                    del slideshows[slug]
                    save_config(cfg)
                    flash("Slideshow deleted.", "success")
            return redirect(url_for("display.admin_slideshows"))

        if action == "duplicate_slideshow":
            src_slug = request.form.get("slug", "").strip()
            src = slideshows.get(src_slug)
            if not src:
                flash("Slideshow not found.", "danger")
                return redirect(url_for("display.admin_slideshows"))
            new_slug = src_slug
            counter = 2
            while new_slug in slideshows:
                new_slug = f"{src_slug}-{counter}"
                counter += 1
            import copy
            slideshows[new_slug] = copy.deepcopy(src)
            slideshows[new_slug]["name"] = f"{src['name']} (copy)"
            save_config(cfg)
            flash("Slideshow duplicated.", "success")
            return redirect(url_for("display.admin_slideshow_edit", ss_slug=new_slug))

    return render_template("admin/slideshows.html", slideshows=slideshows, displays=cfg["displays"])


@bp.route("/admin/slideshows/<ss_slug>", methods=["GET", "POST"])
@permission_required_rw('displays.view', 'displays.edit')
def admin_slideshow_edit(ss_slug):
    cfg = load_config()
    slideshow = get_slideshow(cfg, ss_slug)
    if not slideshow:
        abort(404)
    slides = slideshow.setdefault("slides", [])

    if request.method == "POST":
        action = request.form.get("action")

        if action == "edit_slideshow":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Name is required.", "danger")
                return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))
            try:
                fade = float(request.form.get("fade_duration", "1.4"))
            except (TypeError, ValueError):
                fade = slideshow.get("fade_duration", 1.4)
            slideshow["name"] = name
            slideshow["description"] = request.form.get("description", "").strip()
            slideshow["fade_duration"] = max(0.1, fade)
            save_config(cfg)
            flash("Slideshow updated.", "success")
            return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))

        if action == "add_text":
            slide = {
                "id": next_slide_id(slides),
                "type": "text",
                "headline": request.form.get("headline", "").strip(),
                "subheadline": request.form.get("subheadline", "").strip(),
                "body": request.form.get("body", "").strip(),
                "duration": to_int(request.form.get("duration"), 10),
                "active": True,
                "order": len(slides) + 1,
                "transition": "fade",
            }
            slides.append(slide)
            normalize_orders(slides)
            save_config(cfg)
            flash("Text slide added.", "success")
            return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))

        if action == "add_image":
            file = request.files.get("image_file")
            if not file or not file.filename:
                flash("No image file uploaded.", "danger")
                return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))
            ensure_slide_storage()
            filename = secure_filename(file.filename)
            ext = Path(filename).suffix.lower()
            if ext not in IMAGE_EXTENSIONS:
                flash(f"Image must be one of: {', '.join(sorted(IMAGE_EXTENSIONS))}", "danger")
                return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))
            filename = unique_filename(SLIDES_DIR, filename)
            file.save(SLIDES_DIR / filename)
            slide = {
                "id": next_slide_id(slides),
                "type": "image",
                "file": filename,
                "duration": to_int(request.form.get("duration"), 10),
                "active": True,
                "order": len(slides) + 1,
                "transition": "fade",
            }
            slides.append(slide)
            normalize_orders(slides)
            save_config(cfg)
            flash("Image slide added.", "success")
            return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))

        if action == "add_images_bulk":
            files = request.files.getlist("image_files")
            valid_files = [f for f in files if f and f.filename]
            if not valid_files:
                flash("No image files uploaded.", "danger")
                return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))
            ensure_slide_storage()
            added = 0
            for file in valid_files:
                filename = secure_filename(file.filename)
                ext = Path(filename).suffix.lower()
                if ext not in IMAGE_EXTENSIONS:
                    continue
                filename = unique_filename(SLIDES_DIR, filename)
                file.save(SLIDES_DIR / filename)
                slide = {
                    "id": next_slide_id(slides),
                    "type": "image",
                    "file": filename,
                    "duration": to_int(request.form.get("duration"), 10),
                    "active": True,
                    "order": len(slides) + 1,
                    "transition": "fade",
                }
                slides.append(slide)
                added += 1
            if added:
                normalize_orders(slides)
                save_config(cfg)
                flash(f"Added {added} image slide(s).", "success")
            else:
                flash("No valid image files uploaded.", "danger")
            return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))

        if action == "add_video":
            file = request.files.get("video_file")
            if not file or not file.filename:
                flash("No video file uploaded.", "danger")
                return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))
            ensure_slide_storage()
            filename = secure_filename(file.filename)
            ext = Path(filename).suffix.lower()
            if ext not in VIDEO_EXTENSIONS:
                flash("Video must be MP4 format (.mp4).", "danger")
                return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))
            filename = unique_filename(SLIDES_DIR, filename)
            file.save(SLIDES_DIR / filename)
            slide = {
                "id": next_slide_id(slides),
                "type": "video",
                "file": filename,
                "duration": to_int(request.form.get("duration"), 15),
                "active": True,
                "order": len(slides) + 1,
                "transition": "fade",
            }
            slides.append(slide)
            normalize_orders(slides)
            save_config(cfg)
            flash("Video slide added.", "success")
            return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))

        if action == "set_duration_all":
            requested = to_int(request.form.get("duration"))
            if requested is None or requested <= 0:
                flash("Duration must be a positive number.", "danger")
                return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))
            for s in slides:
                s["duration"] = requested
            save_config(cfg)
            flash(f"Updated duration for {len(slides)} slide(s).", "success")
            return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))

        # Actions targeting a specific slide by ID
        slide_id = to_int(request.form.get("slide_id"))
        slide = next((s for s in slides if s["id"] == slide_id), None) if slide_id else None

        if action in ("toggle_slide", "delete_slide", "set_order", "set_duration", "move_slide") and not slide:
            flash("Slide not found.", "danger")
            return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))

        if action == "toggle_slide":
            slide["active"] = not slide.get("active", True)
            save_config(cfg)
            flash("Slide updated.", "success")

        elif action == "delete_slide":
            slides.remove(slide)
            normalize_orders(slides)
            save_config(cfg)
            flash("Slide deleted.", "success")

        elif action == "set_duration":
            requested = to_int(request.form.get("duration"))
            if requested is None or requested <= 0:
                flash("Duration must be a positive number.", "danger")
            else:
                slide["duration"] = requested
                save_config(cfg)
                flash("Duration updated.", "success")

        elif action == "set_order":
            requested = to_int(request.form.get("order"))
            if requested is None:
                flash("Invalid order.", "danger")
            else:
                sorted_slides = sorted(slides, key=lambda s: s.get("order", s["id"]))
                cur = next((i for i, s in enumerate(sorted_slides) if s["id"] == slide_id), None)
                if cur is not None:
                    requested = max(1, min(requested, len(sorted_slides)))
                    obj = sorted_slides.pop(cur)
                    sorted_slides.insert(requested - 1, obj)
                    for pos, s in enumerate(sorted_slides, start=1):
                        s["order"] = pos
                    save_config(cfg)
                    flash("Order updated.", "success")

        elif action == "move_slide":
            direction = request.form.get("direction")
            sorted_slides = sorted(slides, key=lambda s: s.get("order", s["id"]))
            idx = next((i for i, s in enumerate(sorted_slides) if s["id"] == slide_id), None)
            moved = False
            if idx is not None:
                if direction == "up" and idx > 0:
                    sorted_slides[idx - 1], sorted_slides[idx] = sorted_slides[idx], sorted_slides[idx - 1]
                    moved = True
                elif direction == "down" and idx < len(sorted_slides) - 1:
                    sorted_slides[idx + 1], sorted_slides[idx] = sorted_slides[idx], sorted_slides[idx + 1]
                    moved = True
            if moved:
                for pos, s in enumerate(sorted_slides, start=1):
                    s["order"] = pos
                save_config(cfg)
                flash("Order updated.", "success")
            else:
                flash("Already at boundary.", "info")

        return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))

    sorted_slides = sorted(slides, key=lambda s: s.get("order", s["id"]))
    assigned_to = [
        (slug, d["name"])
        for slug, d in cfg["displays"].items()
        if d.get("assigned_slideshow") == ss_slug
    ]
    return render_template(
        "admin/slideshow_edit.html",
        ss_slug=ss_slug,
        slideshow=slideshow,
        slides=sorted_slides,
        assigned_to=assigned_to,
    )
