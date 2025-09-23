"""Expand recurring service definitions into concrete calendar events."""

from __future__ import annotations
from datetime import datetime, timedelta
from typing import List, Dict, Any
from zoneinfo import ZoneInfo
from dateutil.rrule import rrulestr
import json
from dateutil.parser import isoparse

from .models import ServiceSeries, ServiceOverride, Service, ProgramSlot
from sqlalchemy.orm import Session


def _parse_dates(lst):
    """Normalize JSON stored date strings into ``datetime`` instances."""
    out = []
    if isinstance(lst, str):
        try:
            lst = json.loads(lst)
        except Exception:
            lst = []
    for x in (lst or []):
        try:
            out.append(isoparse(x))
        except Exception:
            try:
                out.append(datetime.fromisoformat(x))
            except Exception:
                pass
    return out


def expand_occurrences(series: ServiceSeries, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Expand a single series into FullCalendar-style dicts within ``[start, end)``."""
    tzname = series.tz or "America/New_York"
    # Treat inbound window as naive local for comparison
    win_start = start.replace(tzinfo=None)
    win_end = end.replace(tzinfo=None)

    base_start = series.dtstart
    base_end = series.dtend
    duration = (base_end - base_start)

    instances = []
    # RRULE expansion (relative to base_start)
    if series.rrule:
        try:
            rule = rrulestr(series.rrule, dtstart=base_start)
            for dt in rule.between(win_start, win_end, inc=True):
                instances.append({"start": dt, "end": dt + duration})
        except Exception:
            # If RRULE invalid, fall back to base instance only
            pass

    # One-off base instance if within window
    if not series.rrule:
        if base_start < win_end and base_end > win_start:
            instances.append({"start": base_start, "end": base_end})

    # RDATE inclusions
    for rdt in _parse_dates(series.rdate):
        if win_start <= rdt < win_end:
            instances.append({"start": rdt, "end": rdt + duration})

    # EXDATE exclusions by date or exact datetime
    ex_dates = _parse_dates(series.exdate)
    ex_set_dt = set([d for d in ex_dates if isinstance(d, datetime)])
    ex_set_day = set([d.date() for d in ex_dates])
    instances = [i for i in instances if (i["start"] not in ex_set_dt and i["start"].date() not in ex_set_day)]

    # Apply overrides
    overrides = {ov.instance_start: ov for ov in (series.overrides or [])}
    out = []
    for inst in instances:
        ov = overrides.get(inst["start"])  # match on original start
        if ov and ov.cancelled:
            continue
        s = ov.new_dtstart if (ov and ov.new_dtstart) else inst["start"]
        e = ov.new_dtend if (ov and ov.new_dtend) else inst["end"]
        title = ov.new_title if (ov and ov.new_title) else series.title
        loc = ov.new_location if (ov and ov.new_location) else (series.location or "")
        out.append({
            "series_id": series.id,
            "service_id": series.service_id,
            "instance_start": inst["start"].isoformat(),
            "title": title,
            "location": loc,
            "category": series.category,
            "start": s.isoformat(),
            "end": e.isoformat(),
            "allDay": bool(series.is_all_day),
            "source": "series",
            "override": bool(ov is not None),
        })
    out.sort(key=lambda x: x["start"])
    return out


def expand_between(session: Session, start: datetime, end: datetime, service_id: int | None = None) -> List[Dict[str, Any]]:
    """Return recurring series instances inside the requested window."""
    events: List[Dict[str, Any]] = []
    q = session.query(ServiceSeries).filter(ServiceSeries.is_active == True)
    if service_id:
        q = q.filter(ServiceSeries.service_id == service_id)
    for s in q.all():
        events.extend(expand_occurrences(s, start, end))
    return events


def expand_slots_between(session: Session, start: datetime, end: datetime, service_id: int | None = None, tzname: str = "America/New_York") -> List[Dict[str, Any]]:
    """Expand baseline ``ProgramSlot`` entries into concrete occurrences."""
    services = session.query(Service).all() if not service_id else [session.get(Service, service_id)]
    services = [s for s in services if s]
    # Preload slots by service
    out: List[Dict[str, Any]] = []
    # Iterate days in window
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        dow = day.weekday()  # 0=Mon
        for svc in services:
            for sl in svc.slots:
                if sl.dow != dow:
                    continue
                try:
                    sh, sm = map(int, (sl.start or "00:00").split(":"))
                    eh, em = map(int, (sl.end or "00:00").split(":"))
                except Exception:
                    continue
                sdt = day.replace(hour=sh, minute=sm)
                edt = day.replace(hour=eh, minute=em)
                out.append({
                    "series_id": None,
                    "service_id": svc.id,
                    "instance_start": sdt.isoformat(),
                    "title": svc.name,
                    "location": svc.location or "",
                    "category": svc.category,
                    "start": sdt.isoformat(),
                    "end": edt.isoformat(),
                    "allDay": False,
                    "source": "slot",
                    "override": False,
                })
        day += timedelta(days=1)
    return out


def merged_occurrences(session: Session, start: datetime, end: datetime, service_id: int | None = None) -> List[Dict[str, Any]]:
    """Mix recurring slots and RRULE series, applying overrides along the way."""
    series_events = expand_between(session, start, end, service_id)
    slot_events = expand_slots_between(session, start, end, service_id)

    # Build index
    by_key: dict[tuple, Dict[str, Any]] = {}
    for ev in slot_events:
        key = ("slot", ev["service_id"], ev["instance_start"])
        by_key[key] = ev
    for ev in series_events:
        key = ("series", ev.get("series_id"), ev["instance_start"])
        by_key[key] = ev

    # Apply overrides
    oquery = session.query(ServiceOverride)
    if service_id:
        oquery = oquery.filter((ServiceOverride.service_id == service_id) | (ServiceOverride.series_id.isnot(None)))
    overrides = oquery.all()
    for ov in overrides:
        inst_iso = ov.instance_start.isoformat()
        if ov.series_id:
            k = ("series", ov.series_id, inst_iso)
            ev = by_key.get(k)
            if ov.cancelled:
                by_key.pop(k, None)
                continue
            if ev:
                # Modify existing series event
                if ov.new_dtstart:
                    ev["start"] = ov.new_dtstart.isoformat()
                if ov.new_dtend:
                    ev["end"] = ov.new_dtend.isoformat()
                if ov.new_title:
                    ev["title"] = ov.new_title
                if ov.new_location:
                    ev["location"] = ov.new_location
                ev["override"] = True
        elif ov.service_id:
            k = ("slot", ov.service_id, inst_iso)
            ev = by_key.get(k)
            if ov.cancelled:
                by_key.pop(k, None)
                continue
            if ev:
                if ov.new_dtstart:
                    ev["start"] = ov.new_dtstart.isoformat()
                if ov.new_dtend:
                    ev["end"] = ov.new_dtend.isoformat()
                if ov.new_title:
                    ev["title"] = ov.new_title
                if ov.new_location:
                    ev["location"] = ov.new_location
                ev["override"] = True

    # Return sorted list
    items = list(by_key.values())
    items.sort(key=lambda x: (x["start"], x.get("service_id") or 0))
    return items
