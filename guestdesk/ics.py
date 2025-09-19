# GuestDesk
# Copyright (c) 2025 Chris Tant
# SPDX-License-Identifier: LicenseRef-GDCL-1.1
from datetime import datetime, timedelta, timezone
from flask import Blueprint, Response, current_app
from icalendar import Calendar, Event

from .services_calendar import merged_occurrences

bp = Blueprint("ics", __name__)


def _to_aware(dt_str: str) -> datetime:
    dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@bp.get("/calendar.ics")
def calendar_feed():
    session_factory = getattr(current_app, "dbs", None)
    if not session_factory:
        return Response("database unavailable", status=503)
    db = session_factory()
    try:
        now = datetime.utcnow()
        window_end = now + timedelta(days=90)
        events = merged_occurrences(db, now, window_end)
    finally:
        db.close()

    cal = Calendar()
    cal.add('prodid', '-//GuestDesk//Calendar//EN')
    cal.add('version', '2.0')

    for ev in events:
        try:
            start = _to_aware(ev.get('start'))
            end = _to_aware(ev.get('end'))
        except Exception:
            continue
        uid = f"{ev.get('service_id')}-{ev.get('instance_start')}@guestdesk"
        item = Event()
        item.add('uid', uid)
        item.add('summary', ev.get('title'))
        item.add('dtstart', start)
        item.add('dtend', end)
        if ev.get('location'):
            item.add('location', ev['location'])
        cal.add_component(item)

    return Response(cal.to_ical(), content_type="text/calendar; charset=utf-8")
