import json
import os
from pathlib import Path
from functools import wraps

from flask import (
    Blueprint, render_template, jsonify,
    request, redirect, url_for, flash, abort,
    session, g, send_from_directory
)
from werkzeug.utils import secure_filename

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
    """Convert ``value`` to ``int`` with a safe fallback."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_config():
    data = None
    if DATA_PATH.exists():
        with DATA_PATH.open() as f:
            data = json.load(f)
    elif LEGACY_DATA_PATH.exists():
        with LEGACY_DATA_PATH.open() as f:
            data = json.load(f)
    if not data:
        data = {"zones": [], "slides": []}
    for slide in data.get("slides", []):
        if slide.get("transition") not in ALLOWED_TRANSITIONS:
            slide["transition"] = "fade"
    return data


def save_config(cfg):
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DATA_PATH.open("w") as f:
        json.dump(cfg, f, indent=2)


def next_id(items, key="id"):
    return (max([item[key] for item in items]) + 1) if items else 1


def get_zone_by_slug(cfg, slug):
    zone = next((z for z in cfg.get("zones", []) if z["slug"] == slug), None)
    if zone is not None:
        zone.setdefault("fade_duration", 1.4)
    return zone


def get_zone_by_id(cfg, zone_id):
    zone = next((z for z in cfg.get("zones", []) if z["id"] == zone_id), None)
    if zone is not None:
        zone.setdefault("fade_duration", 1.4)
    return zone


def normalize_zone_orders(slides: list[dict], zone_id: int):
    zone_slides = sorted(
        [s for s in slides if s["zone_id"] == zone_id],
        key=lambda s: s.get("order", s["id"])
    )
    for idx, slide in enumerate(zone_slides, start=1):
        slide["order"] = idx


def admin_required(fn):
    """Protect admin endpoints using the same session checks as the main app."""
    @wraps(fn)
    def _wrap(*args, **kwargs):
        if session.get("is_admin") or session.get("admin"):
            return fn(*args, **kwargs)
        if getattr(g, "user", None):
            return fn(*args, **kwargs)
        return redirect(url_for("login", next=request.path))
    return _wrap


# ---------- Public API / Display ----------

@bp.route("/api/display-slides/<slug>")
def display_slides_api(slug):
    """
    JSON API used by the TV/PI display page.
    Returns: { "zone": {...}, "slides": [ ... ] }
    """
    cfg = load_config()
    zone = get_zone_by_slug(cfg, slug)
    if not zone or not zone.get("active", True):
        abort(404)

    slides = [
        s for s in cfg.get("slides", [])
        if s.get("zone_id") == zone["id"] and s.get("active", True)
    ]
    slides.sort(key=lambda s: s.get("order", s["id"]))
    enriched = []
    for slide in slides:
        payload = dict(slide)
        payload["transition"] = slide.get("transition") or "cut"
        if slide.get("file") and slide.get("type") in ("image", "video"):
            payload["file_url"] = url_for("display.display_media", filename=slide["file"])
        enriched.append(payload)
    return jsonify({"zone": zone, "slides": enriched})


@bp.route("/display/<slug>")
def display_screen(slug):
    """
    Public page for TVs/players to open fullscreen.
    Example: https://guestdesk.info/display/lobby1
    """
    cfg = load_config()
    zone = get_zone_by_slug(cfg, slug)
    if not zone or not zone.get("active", True):
        abort(404)
    preview = request.args.get("preview", "").lower() in {"1", "true", "yes", "on"}
    preview_duration = None
    if preview:
        try:
            preview_duration = float(request.args.get("duration", "2.5"))
        except (TypeError, ValueError):
            preview_duration = 2.5
        preview_duration = max(0.5, preview_duration)
    return render_template(
        "display_screen.html",
        zone=zone,
        preview=preview,
        preview_duration=preview_duration,
    )


@bp.route("/displays/<slug>")
def public_display(slug):
    """
    Permanent public URL for managed display endpoints (Pi bookmark target).
    Example: https://guestdesk.info/displays/lobby-2
    """
    cfg = load_config()
    zone = get_zone_by_slug(cfg, slug)
    if not zone or not zone.get("active", True):
        abort(404)
    preview = request.args.get("preview", "").lower() in {"1", "true", "yes", "on"}
    preview_duration = None
    if preview:
        try:
            preview_duration = float(request.args.get("duration", "2.5"))
        except (TypeError, ValueError):
            preview_duration = 2.5
        preview_duration = max(0.5, preview_duration)
    return render_template(
        "display_screen.html",
        zone=zone,
        preview=preview,
        preview_duration=preview_duration,
    )


@bp.route("/display-media/<path:filename>")
def display_media(filename):
    """Serve uploaded slide assets from the writable data directory (with legacy fallback)."""
    for directory in (SLIDES_DIR, LEGACY_SLIDES_DIR):
        candidate = directory / filename
        if candidate.exists():
            return send_from_directory(directory, filename)
    abort(404)


# ---------- Admin: Zones + Slides ----------

@bp.route("/admin/displays", methods=["GET", "POST"])
@admin_required
def display_admin():
    cfg = load_config()
    zones = cfg.setdefault("zones", [])
    slides = cfg.setdefault("slides", [])

    # ----- Handle POST actions -----
    if request.method == "POST":
        action = request.form.get("action")

        # ----- ZONE ACTIONS -----
        if action == "add_zone":
            name = request.form.get("name", "").strip()
            slug = request.form.get("slug", "").strip()
            if not name or not slug:
                flash("Name and slug are required.", "danger")
            elif any(z["slug"] == slug for z in zones):
                flash("Slug must be unique.", "danger")
            else:
                fade = request.form.get("fade_duration")
                try:
                    fade_val = float(fade)
                except (TypeError, ValueError):
                    fade_val = 1.4
                zone = {
                    "id": next_id(zones),
                    "name": name,
                    "slug": slug,
                    "location": request.form.get("location", "").strip(),
                    "active": True,
                    "fade_duration": max(0.1, fade_val),
                }
                zones.append(zone)
                save_config(cfg)
                flash("Display created.", "success")
                return redirect(url_for("display.display_admin", zone=slug))

            return redirect(url_for("display.display_admin"))

        if action == "edit_zone":
            zone_id = to_int(request.form.get("zone_id"))
            if zone_id is None:
                flash("Invalid display.", "danger")
                return redirect(url_for("display.display_admin"))
            zone = next((z for z in zones if z["id"] == zone_id), None)
            if not zone:
                flash("Display not found.", "danger")
                return redirect(url_for("display.display_admin"))

            new_name = request.form.get("name", "").strip()
            new_slug = request.form.get("slug", "").strip()
            new_fade_raw = request.form.get("fade_duration")
            try:
                new_fade = float(new_fade_raw)
            except (TypeError, ValueError):
                new_fade = zone.get("fade_duration", 1.4)
            new_fade = max(0.1, new_fade)

            if not new_name or not new_slug:
                flash("Name and slug are required.", "danger")
            elif any(z["slug"] == new_slug and z["id"] != zone_id for z in zones):
                flash("Slug must be unique.", "danger")
            else:
                zone["name"] = new_name
                zone["slug"] = new_slug
                zone["location"] = request.form.get("location", "").strip()
                zone["fade_duration"] = new_fade
                save_config(cfg)
                flash("Display updated.", "success")
                return redirect(url_for("display.display_admin", zone=new_slug))

            return redirect(url_for("display.display_admin", zone=zone["slug"]))

        if action == "toggle_zone":
            zone_id = to_int(request.form.get("zone_id"))
            if zone_id is None:
                flash("Invalid display.", "danger")
                return redirect(url_for("display.display_admin"))
            zone = next((z for z in zones if z["id"] == zone_id), None)
            if zone:
                zone["active"] = not zone.get("active", True)
                save_config(cfg)
                flash("Display updated.", "success")
                return redirect(url_for("display.display_admin", zone=zone["slug"]))
            return redirect(url_for("display.display_admin"))

        if action == "delete_zone":
            zone_id = to_int(request.form.get("zone_id"))
            if zone_id is None:
                flash("Invalid display.", "danger")
                return redirect(url_for("display.display_admin"))
            zone = next((z for z in zones if z["id"] == zone_id), None)
            if not zone:
                flash("Display not found.", "danger")
                return redirect(url_for("display.display_admin"))

            zone_slides = [s for s in slides if s["zone_id"] == zone_id]
            if zone_slides:
                flash("Cannot delete display with slides. Delete slides first.", "danger")
            else:
                zones.remove(zone)
                save_config(cfg)
                flash("Display deleted.", "success")

            return redirect(url_for("display.display_admin"))

        # ----- SLIDE ACTIONS -----
        if action in (
            "add_text",
            "add_image",
            "add_images_bulk",
            "add_video",
            "toggle_slide",
            "delete_slide",
            "move_slide",
            "set_order",
            "set_duration",
            "set_duration_all",
        ):
            selected_zone_slug = request.form.get("selected_zone_slug")
            zone = get_zone_by_slug(cfg, selected_zone_slug)
            if not zone:
                flash("Display not found.", "danger")
                return redirect(url_for("display.display_admin"))

            zone_id = zone["id"]
            zone_slides = [s for s in slides if s["zone_id"] == zone_id]

            if action == "add_text":
                slide = {
                    "id": next_id(slides),
                    "zone_id": zone_id,
                    "type": "text",
                    "headline": request.form.get("headline", "").strip(),
                    "subheadline": request.form.get("subheadline", "").strip(),
                    "body": request.form.get("body", "").strip(),
                    "duration": to_int(request.form.get("duration"), 10),
                    "active": True,
                    "order": to_int(request.form.get("order"), len(zone_slides) + 1),
                    "transition": clean_transition(request.form.get("transition")),
                }
                slides.append(slide)
                normalize_zone_orders(slides, zone_id)
                save_config(cfg)
                flash("Text slide added.", "success")
                return redirect(url_for("display.display_admin", zone=selected_zone_slug))

            if action == "add_image":
                file = request.files.get("image_file")
                if not file or not file.filename:
                    flash("No image file uploaded.", "danger")
                    return redirect(url_for("display.display_admin", zone=selected_zone_slug))

                ensure_slide_storage()
                filename = secure_filename(file.filename)
                ext = Path(filename).suffix.lower()
                if ext not in IMAGE_EXTENSIONS:
                    flash(f"Image must be one of: {', '.join(sorted(IMAGE_EXTENSIONS))}", "danger")
                    return redirect(url_for("display.display_admin", zone=selected_zone_slug))
                filename = unique_filename(SLIDES_DIR, filename)
                filepath = SLIDES_DIR / filename
                file.save(filepath)

                slide = {
                    "id": next_id(slides),
                    "zone_id": zone_id,
                    "type": "image",
                    "file": filename,
                    "duration": to_int(request.form.get("duration"), 10),
                    "active": True,
                    "order": to_int(request.form.get("order"), len(zone_slides) + 1),
                    "transition": clean_transition(request.form.get("transition")),
                }
                slides.append(slide)
                normalize_zone_orders(slides, zone_id)
                save_config(cfg)
                flash("Image slide added.", "success")
                return redirect(url_for("display.display_admin", zone=selected_zone_slug))

            if action == "add_images_bulk":
                files = request.files.getlist("image_files")
                valid_files = [f for f in files if f and f.filename]
                if not valid_files:
                    flash("No image files uploaded.", "danger")
                    return redirect(url_for("display.display_admin", zone=selected_zone_slug))

                ensure_slide_storage()
                added = 0
                start_order = len(zone_slides) + 1
                for offset, file in enumerate(valid_files):
                    filename = secure_filename(file.filename)
                    ext = Path(filename).suffix.lower()
                    if ext not in IMAGE_EXTENSIONS:
                        continue
                    filename = unique_filename(SLIDES_DIR, filename)
                    file.save(SLIDES_DIR / filename)
                    slide = {
                        "id": next_id(slides),
                        "zone_id": zone_id,
                        "type": "image",
                        "file": filename,
                        "duration": to_int(request.form.get("duration"), 10),
                        "active": True,
                        "order": start_order + added,
                        "transition": clean_transition(request.form.get("transition")),
                    }
                    slides.append(slide)
                    added += 1

                if added:
                    normalize_zone_orders(slides, zone_id)
                    save_config(cfg)
                    flash(f"Added {added} image slide(s).", "success")
                else:
                    flash("No valid image files uploaded.", "danger")
                return redirect(url_for("display.display_admin", zone=selected_zone_slug))

            if action == "add_video":
                file = request.files.get("video_file")
                if not file or not file.filename:
                    flash("No video file uploaded.", "danger")
                    return redirect(url_for("display.display_admin", zone=selected_zone_slug))

                ensure_slide_storage()
                filename = secure_filename(file.filename)
                ext = Path(filename).suffix.lower()
                if ext not in VIDEO_EXTENSIONS:
                    flash("Video must be MP4 format (.mp4).", "danger")
                    return redirect(url_for("display.display_admin", zone=selected_zone_slug))

                filename = unique_filename(SLIDES_DIR, filename)
                filepath = SLIDES_DIR / filename
                file.save(filepath)

                slide = {
                    "id": next_id(slides),
                    "zone_id": zone_id,
                    "type": "video",
                    "file": filename,
                    "duration": to_int(request.form.get("duration"), 15),
                    "active": True,
                    "order": to_int(request.form.get("order"), len(zone_slides) + 1),
                    "transition": clean_transition(request.form.get("transition")),
                }
                slides.append(slide)
                normalize_zone_orders(slides, zone_id)
                save_config(cfg)
                flash("Video slide added.", "success")
                return redirect(url_for("display.display_admin", zone=selected_zone_slug))

            if action == "set_duration_all":
                requested = to_int(request.form.get("duration"))
                if requested is None or requested <= 0:
                    flash("Duration must be a positive number.", "danger")
                    return redirect(url_for("display.display_admin", zone=selected_zone_slug))
                affected = 0
                for slide_obj in slides:
                    if slide_obj.get("zone_id") == zone_id:
                        slide_obj["duration"] = requested
                        affected += 1
                save_config(cfg)
                flash(f"Updated duration for {affected} slide(s).", "success")
                return redirect(url_for("display.display_admin", zone=selected_zone_slug))

            slide_id = to_int(request.form.get("slide_id"))
            if slide_id is None:
                flash("Invalid slide selection.", "danger")
                return redirect(url_for("display.display_admin", zone=selected_zone_slug))
            slide = next(
                (s for s in slides if s["id"] == slide_id and s["zone_id"] == zone_id),
                None,
            )
            if not slide:
                flash("Slide not found.", "danger")
                return redirect(url_for("display.display_admin", zone=selected_zone_slug))

            if action == "toggle_slide":
                slide["active"] = not slide.get("active", True)
                save_config(cfg)
                flash("Slide updated.", "success")

            elif action == "delete_slide":
                slides.remove(slide)
                normalize_zone_orders(slides, zone_id)
                save_config(cfg)
                flash("Slide deleted.", "success")

            elif action == "move_slide":
                direction = request.form.get("direction")
                zone_slides_sorted = sorted(
                    [s for s in slides if s["zone_id"] == zone_id],
                    key=lambda s: s.get("order", s["id"])
                )
                idx = next((i for i, s in enumerate(zone_slides_sorted) if s["id"] == slide_id), None)
                if idx is None:
                    flash("Slide not found.", "danger")
                    return redirect(url_for("display.display_admin", zone=selected_zone_slug))
                moved = False
                if direction == "up" and idx > 0:
                    zone_slides_sorted[idx - 1], zone_slides_sorted[idx] = zone_slides_sorted[idx], zone_slides_sorted[idx - 1]
                    moved = True
                elif direction == "down" and idx < len(zone_slides_sorted) - 1:
                    zone_slides_sorted[idx + 1], zone_slides_sorted[idx] = zone_slides_sorted[idx], zone_slides_sorted[idx + 1]
                    moved = True
                if moved:
                    for position, slide_obj in enumerate(zone_slides_sorted, start=1):
                        slide_obj["order"] = position
                    save_config(cfg)
                    flash("Slide order updated.", "success")
                else:
                    flash("Slide already at boundary.", "info")

            elif action == "set_order":
                requested = to_int(request.form.get("order"))
                if requested is None:
                    flash("Invalid order.", "danger")
                    return redirect(url_for("display.display_admin", zone=selected_zone_slug))
                zone_slides_sorted = sorted(
                    [s for s in slides if s["zone_id"] == zone_id],
                    key=lambda s: s.get("order", s["id"])
                )
                current_idx = next((i for i, s in enumerate(zone_slides_sorted) if s["id"] == slide_id), None)
                if current_idx is None:
                    flash("Slide not found.", "danger")
                    return redirect(url_for("display.display_admin", zone=selected_zone_slug))
                requested = max(1, min(requested, len(zone_slides_sorted)))
                slide_obj = zone_slides_sorted.pop(current_idx)
                zone_slides_sorted.insert(requested - 1, slide_obj)
                for position, obj in enumerate(zone_slides_sorted, start=1):
                    obj["order"] = position
                save_config(cfg)
                flash("Slide order updated.", "success")

            elif action == "set_duration":
                requested = to_int(request.form.get("duration"))
                if requested is None or requested <= 0:
                    flash("Duration must be a positive number.", "danger")
                    return redirect(url_for("display.display_admin", zone=selected_zone_slug))
                slide["duration"] = requested
                save_config(cfg)
                flash("Slide duration updated.", "success")

            elif action == "set_duration_all":
                requested = to_int(request.form.get("duration"))
                if requested is None or requested <= 0:
                    flash("Duration must be a positive number.", "danger")
                    return redirect(url_for("display.display_admin", zone=selected_zone_slug))
                affected = 0
                for slide_obj in slides:
                    if slide_obj.get("zone_id") == zone_id:
                        slide_obj["duration"] = requested
                        affected += 1
                save_config(cfg)
                flash(f"Updated duration for {affected} slide(s).", "success")

            return redirect(url_for("display.display_admin", zone=selected_zone_slug))

    # ----- GET: render admin UI -----
    selected_slug = request.args.get("zone")
    if zones and not selected_slug:
        selected_slug = zones[0]["slug"]

    selected_zone = get_zone_by_slug(cfg, selected_slug) if selected_slug else None

    if selected_zone:
        zone_slides = sorted(
            [s for s in slides if s["zone_id"] == selected_zone["id"]],
            key=lambda s: s.get("order", s["id"])
        )
    else:
        zone_slides = []

    return render_template(
        "admin_display.html",
        zones=zones,
        selected_zone=selected_zone,
        slides=zone_slides,
    )
