import json
import os
import re
from math import ceil
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from functools import wraps
from urllib.parse import urlparse, parse_qs
from uuid import uuid4

from flask import (
    Blueprint, render_template, jsonify,
    request, redirect, url_for, flash, abort,
    session, g, send_from_directory, current_app
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
SCHEDULE_PAGE_UNITS = 6
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
VIDEO_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


def youtube_video_id(raw: str) -> str:
    """Accept an individual YouTube URL, never arbitrary iframe HTML or hosts."""
    value = (raw or "").strip()
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        invalid_authority = bool(parsed.username or parsed.password or parsed.port)
    except ValueError:
        raise ValueError("Enter a valid individual YouTube video URL.") from None
    if parsed.scheme not in {"http", "https"} or invalid_authority:
        raise ValueError("Enter a YouTube watch, short, or embed URL.")
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com",
                "www.youtube-nocookie.com"}:
        parts = parsed.path.strip("/").split("/")
        if parsed.path == "/watch" and host != "www.youtube-nocookie.com":
            values = parse_qs(parsed.query).get("v", [])
            candidate = values[0] if len(values) == 1 else ""
        elif len(parts) == 2 and parts[0] in (
            {"embed"} if host == "www.youtube-nocookie.com" else {"embed", "shorts"}
        ):
            candidate = parts[1]
        else:
            candidate = ""
    elif host == "youtu.be":
        parts = parsed.path.strip("/").split("/")
        candidate = parts[0] if len(parts) == 1 else ""
    else:
        candidate = ""
    if not YOUTUBE_ID.fullmatch(candidate):
        raise ValueError("Enter a valid individual YouTube video URL.")
    return candidate


def eligible_videos(video_list: dict | None) -> list[dict]:
    """Expose only entries approved for unattended playback."""
    result = []
    for entry in (video_list or {}).get("entries", []):
        video_id = entry.get("video_id")
        if (entry.get("enabled", True) and entry.get("reviewed", False)
                and isinstance(video_id, str) and YOUTUBE_ID.fullmatch(video_id)):
            result.append({"title": entry.get("title", ""), "video_id": video_id})
    return result


def validate_video_window(form, cfg: dict, other_windows: list[dict],
                          window_id: str | None = None) -> dict:
    name = (form.get("name") or "").strip()
    list_slug = form.get("video_list") or ""
    start = form.get("start") or ""
    end = form.get("end") or ""
    raw_days = form.getlist("days")
    if not name:
        raise ValueError("Window name is required.")
    if list_slug not in cfg["video_lists"]:
        raise ValueError("Choose an existing video list.")
    if not VIDEO_TIME.fullmatch(start) or not VIDEO_TIME.fullmatch(end) or end <= start:
        raise ValueError("Enter same-day times with the end after the start. Split overnight programming into two windows.")
    if not raw_days or any(day not in {str(i) for i in range(7)} for day in raw_days):
        raise ValueError("Choose at least one valid weekday.")
    days = sorted({int(day) for day in raw_days})
    enabled = "enabled" in form
    if enabled:
        for other in other_windows:
            if other.get("id") == window_id or not other.get("enabled", False):
                continue
            if set(days).intersection(other.get("days", [])) and start < other.get("end", "") and other.get("start", "") < end:
                raise ValueError(f"This window overlaps enabled window “{other.get('name', 'another window')}” on a selected weekday.")
    return {"id": window_id or uuid4().hex, "name": name, "enabled": enabled,
            "days": days, "start": start, "end": end, "video_list": list_slug}


def active_video_window(windows: list[dict], at: datetime) -> dict | None:
    """Local civil time; start inclusive, end exclusive, including DST repeats."""
    clock = at.strftime("%H:%M")
    for window in windows:
        if (window.get("enabled", False) and at.weekday() in window.get("days", [])
                and window.get("start", "") <= clock < window.get("end", "")):
            return window
    return None


def playback_windows(display: dict, cfg: dict) -> list[dict]:
    result = []
    for window in display.get("video_windows", []):
        if (not window.get("enabled", False) or not window.get("id")
                or not VIDEO_TIME.fullmatch(window.get("start") or "")
                or not VIDEO_TIME.fullmatch(window.get("end") or "")):
            continue
        result.append({"id": window["id"], "name": window.get("name", ""),
                       "days": window.get("days", []), "start": window.get("start"),
                       "end": window.get("end"),
                       "videos": eligible_videos(cfg["video_lists"].get(window.get("video_list")))})
    return result


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

    return {"displays": displays, "slideshows": slideshows, "video_lists": {}}


def load_config() -> dict:
    raw = None
    for path in (DATA_PATH, LEGACY_DATA_PATH):
        if path.exists():
            with path.open() as f:
                raw = json.load(f)
            break

    if not raw:
        return {"displays": {}, "slideshows": {}, "video_lists": {}}

    # Migrate old zone-based format
    if "zones" in raw or "slides" in raw:
        raw = _migrate_legacy(raw)
        save_config(raw)
        return raw

    raw.setdefault("displays", {})
    raw.setdefault("slideshows", {})
    raw.setdefault("video_lists", {})
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


def _duration(value, label):
    """Validate a built-in duration without silently changing bad input."""
    if not re.fullmatch(r"[0-9]+", str(value or "")) or not 1 <= int(value) <= 300:
        raise ValueError(f"{label} must be a whole number from 1 to 300 seconds.")
    return int(value)


def _local_now():
    tzname = current_app.config.get("BABEL_DEFAULT_TIMEZONE", "America/New_York")
    try:
        tz = ZoneInfo(tzname)
    except (KeyError, ValueError):
        tzname, tz = "UTC", timezone.utc
    return datetime.now(tz), tzname, tz


def today_services(now, tz):
    """Public occurrence projection for the local day, including overnight events."""
    from .models import ServiceSeries, ServiceOverride
    from .services_calendar import merged_occurrences

    day_start = datetime.combine(now.date(), time.min, tzinfo=tz)
    day_end = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=tz)
    db = current_app.dbs()
    # Recurrence expansion selects starts, so include earlier starts that may
    # still overlap today. Also include originals moved into today by override.
    series = db.query(ServiceSeries).filter(ServiceSeries.is_active.is_(True)).all()
    spans = {s.id: s.dtend - s.dtstart for s in series}
    max_span = max(spans.values(), default=timedelta(0))
    window_start = day_start - max_span - timedelta(days=1)
    window_end = day_end
    for ov in db.query(ServiceOverride).filter(ServiceOverride.cancelled.is_(False)).all():
        if ov.series_id not in spans:
            continue
        moved_start = ov.new_dtstart or ov.instance_start
        moved_end = ov.new_dtend or moved_start + spans[ov.series_id]
        if moved_start < day_end.replace(tzinfo=None) and moved_end > day_start.replace(tzinfo=None):
            window_start = min(window_start, ov.instance_start.replace(tzinfo=tz))
            window_end = max(window_end, ov.instance_start.replace(tzinfo=tz) + timedelta(seconds=1))
    events = merged_occurrences(db, window_start, window_end, tzname=getattr(tz, "key", "UTC"))
    result = []
    for ev in events:
        try:
            start = datetime.fromisoformat(ev["start"])
            end = datetime.fromisoformat(ev["end"])
            if start.tzinfo is None:
                start = start.replace(tzinfo=tz)
            if end.tzinfo is None:
                end = end.replace(tzinfo=tz)
            start, end = start.astimezone(tz), end.astimezone(tz)
        except (KeyError, TypeError, ValueError):
            continue
        if start < day_end and end > day_start:
            result.append({
                "title": ev.get("title") or "Service",
                "location": ev.get("location") or "",
                "start": start.isoformat(),
                "end": end.isoformat(),
            })
    result.sort(key=lambda ev: (datetime.fromisoformat(ev["start"]).timestamp(), ev["title"]))
    return result


def paginate_services(events):
    """Keep typical days together while reserving room for wrapped text."""
    pages = []
    used = 0
    for event in events:
        title_lines = max(1, ceil(len(event["title"]) / 34))
        location_lines = ceil(len(event["location"]) / 45) if event["location"] else 0
        units = min(SCHEDULE_PAGE_UNITS, ceil(
            1 + .6 * (title_lines - 1) + .45 * max(0, location_lines - 1)
        ))
        if not pages or used + units > SCHEDULE_PAGE_UNITS:
            pages.append([])
            used = 0
        pages[-1].append(event)
        used += units
    return pages


def build_playlist(slideshow):
    """Build generated and manual entries for either playback endpoint."""
    now, tzname, tz = _local_now()
    slides = []
    schedule_state = "disabled"
    if slideshow.get("clock_enabled", False):
        slides.append({"type": "clock", "key": "builtin:clock",
                       "duration": slideshow.get("clock_duration", 15), "transition": "fade"})
    if slideshow.get("schedule_enabled", False):
        try:
            events = today_services(now, tz)
            schedule_state = "empty" if not events else "ok"
            pages = paginate_services(events)
            for page, page_events in enumerate(pages, start=1):
                slides.append({"type": "schedule", "key": f"builtin:schedule:{page}",
                               "duration": slideshow.get("schedule_duration", 15),
                               "transition": "fade", "events": page_events,
                               "page": page, "pages": len(pages)})
        except Exception:
            current_app.logger.exception("Unable to refresh display services")
            schedule_state = "unavailable"
    for slide in active_slides(slideshow):
        payload = dict(slide)
        payload["transition"] = slide.get("transition") or "fade"
        if slide.get("file") and slide.get("type") in ("image", "video"):
            payload["file_url"] = url_for("display.display_media", filename=slide["file"])
        slides.append(payload)
    return slides, {"timezone": tzname, "server_time": now.isoformat(),
                    "local_date": now.date().isoformat(), "schedule_state": schedule_state}


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
    public_display_data = {k: v for k, v in display.items() if k != "video_windows"}
    video_windows = playback_windows(display, cfg)
    if not slideshow:
        now, tzname, _ = _local_now()
        return jsonify({"display": public_display_data, "slideshow": None, "slides": [],
                        "video_windows": video_windows, "timezone": tzname,
                        "server_time": now.isoformat(), "local_date": now.date().isoformat(),
                        "schedule_state": "disabled"})

    slides, metadata = build_playlist(slideshow)

    return jsonify({
        "display": public_display_data,
        "slideshow": {k: v for k, v in slideshow.items() if k != "slides"},
        "slides": slides,
        "video_windows": video_windows,
        **metadata,
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
        video_enabled=not preview,
    )


@bp.route("/api/slideshow-slides/<ss_slug>")
def slideshow_slides_api(ss_slug):
    """JSON API for slideshow preview — bypasses display lookup."""
    cfg = load_config()
    slideshow = get_slideshow(cfg, ss_slug)
    if not slideshow:
        abort(404)
    slides, metadata = build_playlist(slideshow)
    return jsonify({
        "display": {"name": slideshow["name"], "active": True},
        "slideshow": {k: v for k, v in slideshow.items() if k != "slides"},
        "slides": slides,
        **metadata,
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
        video_enabled=False,
    )


# ---------- Admin: Displays ----------

@bp.route("/admin/displays/youtube-test", methods=["GET", "POST"])
@permission_required_rw('displays.view', 'displays.edit')
def youtube_playback_test():
    """Staff-only device test. It does not alter display configuration."""
    cfg = load_config()
    displays = {slug: item for slug, item in cfg["displays"].items()
                if item.get("active", True)}
    if request.method == "POST":
        display_slug = request.form.get("display_slug", "")
        if display_slug not in displays:
            flash("Select an active display for the fallback slideshow.", "danger")
            return redirect(url_for("display.youtube_playback_test"))
        try:
            video_id = youtube_video_id(request.form.get("video_url", ""))
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("display.youtube_playback_test"))
        return redirect(url_for("display.youtube_playback_test",
                                video_id=video_id, display_slug=display_slug))

    video_id = request.args.get("video_id", "")
    display_slug = request.args.get("display_slug", "")
    if video_id and not YOUTUBE_ID.fullmatch(video_id):
        abort(400)
    if video_id and display_slug not in displays:
        abort(400)
    return render_template("admin/youtube_playback_test.html", displays=displays,
                           video_id=video_id, display_slug=display_slug)


@bp.route("/admin/video-lists", methods=["GET", "POST"])
@permission_required_rw('displays.view', 'displays.edit')
def admin_video_lists():
    cfg = load_config()
    lists = cfg["video_lists"]
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_list":
            name = (request.form.get("name") or "").strip()
            slug = slugify(name)
            if not name or not slug:
                flash("List name is required.", "danger")
            elif slug in lists:
                flash("A video list with that name already exists.", "danger")
            else:
                lists[slug] = {"name": name, "entries": []}
                save_config(cfg)
                return redirect(url_for("display.admin_video_list_edit", list_slug=slug))
        elif action == "delete_list":
            slug = request.form.get("slug", "")
            if slug not in lists:
                flash("Video list not found.", "danger")
            else:
                uses = [f"{d.get('name', display_slug)} — {w.get('name', 'window')}"
                        for display_slug, d in cfg["displays"].items()
                        for w in d.get("video_windows", []) if w.get("video_list") == slug]
                if uses:
                    flash("Unassign this list before deleting it. Used by: " + ", ".join(uses), "danger")
                else:
                    del lists[slug]
                    save_config(cfg)
                    flash("Video list deleted.", "success")
        return redirect(url_for("display.admin_video_lists"))
    return render_template("admin/video_lists.html", video_lists=lists)


@bp.route("/admin/video-lists/<list_slug>", methods=["GET", "POST"])
@permission_required_rw('displays.view', 'displays.edit')
def admin_video_list_edit(list_slug):
    cfg = load_config()
    video_list = cfg["video_lists"].get(list_slug)
    if video_list is None:
        abort(404)
    entries = video_list.setdefault("entries", [])
    if request.method == "POST":
        action = request.form.get("action")
        if action == "rename_list":
            name = (request.form.get("name") or "").strip()
            if not name:
                flash("List name is required.", "danger")
            else:
                video_list["name"] = name
                save_config(cfg)
                flash("List renamed.", "success")
        elif action == "add_entry":
            title = (request.form.get("title") or "").strip()
            try:
                video_id = youtube_video_id(request.form.get("video_url", ""))
                if not title:
                    raise ValueError("A staff-entered video title is required.")
            except ValueError as exc:
                flash(str(exc), "danger")
            else:
                entries.append({"id": uuid4().hex, "title": title, "video_id": video_id,
                                "enabled": "enabled" in request.form,
                                "reviewed": "reviewed" in request.form})
                save_config(cfg)
                flash("Video added.", "success")
        elif action in {"edit_entry", "delete_entry", "move_up", "move_down"}:
            entry_id = request.form.get("entry_id", "")
            index = next((i for i, entry in enumerate(entries) if entry.get("id") == entry_id), None)
            if index is None:
                flash("Video entry not found.", "danger")
            elif action == "delete_entry":
                entries.pop(index)
                save_config(cfg)
                flash("Video removed.", "success")
            elif action in {"move_up", "move_down"}:
                other = index + (-1 if action == "move_up" else 1)
                if 0 <= other < len(entries):
                    entries[index], entries[other] = entries[other], entries[index]
                    save_config(cfg)
            else:
                title = (request.form.get("title") or "").strip()
                try:
                    video_id = youtube_video_id(request.form.get("video_url", ""))
                    if not title:
                        raise ValueError("A staff-entered video title is required.")
                except ValueError as exc:
                    flash(str(exc), "danger")
                else:
                    entry = entries[index]
                    changed = entry.get("video_id") != video_id
                    entry.update(title=title, video_id=video_id,
                                 enabled="enabled" in request.form,
                                 reviewed=("reviewed" in request.form and not changed))
                    save_config(cfg)
                    flash("Video updated. Review was cleared because the video changed."
                          if changed else "Video updated.", "success")
        return redirect(url_for("display.admin_video_list_edit", list_slug=list_slug))
    return render_template("admin/video_list_edit.html", list_slug=list_slug,
                           video_list=video_list, entries=entries)


@bp.route("/admin/displays/<display_slug>/programming", methods=["GET", "POST"])
@permission_required_rw('displays.view', 'displays.edit')
def admin_display_programming(display_slug):
    cfg = load_config()
    display = cfg["displays"].get(display_slug)
    if display is None:
        abort(404)
    windows = display.setdefault("video_windows", [])
    if request.method == "POST":
        action = request.form.get("action")
        if action in {"add_window", "edit_window"}:
            window_id = request.form.get("window_id", "") if action == "edit_window" else None
            index = next((i for i, item in enumerate(windows)
                          if item.get("id") == window_id), None) if window_id else None
            if action == "edit_window" and index is None:
                flash("Window not found.", "danger")
            else:
                try:
                    window = validate_video_window(request.form, cfg, windows, window_id)
                except ValueError as exc:
                    flash(str(exc), "danger")
                else:
                    if index is None:
                        windows.append(window)
                    else:
                        windows[index] = window
                    save_config(cfg)
                    flash("Scheduled window saved.", "success")
        elif action == "delete_window":
            window_id = request.form.get("window_id", "")
            old_count = len(windows)
            windows[:] = [item for item in windows if item.get("id") != window_id]
            if len(windows) != old_count:
                save_config(cfg)
                flash("Scheduled window removed.", "success")
        return redirect(url_for("display.admin_display_programming", display_slug=display_slug))
    timezone_name = current_app.config.get("BABEL_DEFAULT_TIMEZONE", "America/New_York")
    return render_template("admin/display_programming.html", display=display,
                           display_slug=display_slug, windows=windows,
                           video_lists=cfg["video_lists"], timezone_name=timezone_name)

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
                    "video_windows": [],
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
                    "clock_enabled": False,
                    "clock_duration": 15,
                    "schedule_enabled": False,
                    "schedule_duration": 15,
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

        if action == "edit_builtins":
            try:
                clock_duration = _duration(request.form.get("clock_duration"), "Date & time duration")
                schedule_duration = _duration(request.form.get("schedule_duration"), "Services page duration")
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("display.admin_slideshow_edit", ss_slug=ss_slug))
            slideshow.update(clock_enabled="clock_enabled" in request.form,
                             clock_duration=clock_duration,
                             schedule_enabled="schedule_enabled" in request.form,
                             schedule_duration=schedule_duration)
            save_config(cfg)
            flash("Built-in slide settings updated.", "success")
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
