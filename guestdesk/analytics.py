from __future__ import annotations
import hmac, hashlib
from datetime import datetime
from flask import Blueprint, current_app, request, jsonify
from user_agents import parse as ua_parse
from sqlalchemy.orm import sessionmaker

try:
    from guestdesk.models import AnalyticsEvent, Base
except Exception:  # pragma: no cover
    from guestdesk.app import AnalyticsEvent, Base  # type: ignore

analytics_bp = Blueprint("analytics", __name__, url_prefix="/analytics")

SessionLocal = None  # set in init_analytics()


def _ip_hash(ip: str, salt: str) -> str | None:
    if not ip or not salt:
        return None
    h = hmac.new(salt.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()
    return h[:32]


@analytics_bp.post("/collect")
def collect():
    if not current_app.config.get("ANALYTICS_ENABLED", True):
        return jsonify({"ok": True, "disabled": True}), 200

    # Respect Do-Not-Track
    dnt = request.headers.get("DNT") or request.headers.get("X-Do-Not-Track")
    if str(dnt).strip() in ("1", "yes", "true"):
        return jsonify({"ok": True, "dnt": True}), 200

    data = request.get_json(silent=True) or {}
    now = datetime.utcnow()

    def ts(ms, default):
        try:
            return datetime.utcfromtimestamp(int(ms) / 1000.0)
        except Exception:
            return default

    start = ts(data.get("started_at_ms"), now)
    end = ts(data.get("ended_at_ms"), now)
    duration_ms = max(0, int((end - start).total_seconds() * 1000))

    ua_raw = request.headers.get("User-Agent") or ""
    ua = ua_parse(ua_raw)
    if ua.is_mobile:
        device = "mobile"
    elif ua.is_tablet:
        device = "tablet"
    elif ua.is_pc:
        device = "pc"
    elif ua.is_bot:
        device = "bot"
    else:
        device = "other"

    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
    salt = current_app.config.get("ANALYTICS_IP_SALT", "")
    ip_hash = _ip_hash(ip, salt) if salt else None

    ev = AnalyticsEvent(
        client_id=(data.get("client_id") or None),
        session_id=(data.get("session_id") or None),
        path=(data.get("path") or "/"),
        referrer=(data.get("referrer") or None),
        started_at=start,
        ended_at=end,
        duration_ms=duration_ms,
        ip_hash=ip_hash,
        user_agent=ua_raw,
        device=device,
        os=str(ua.os),
        browser=str(ua.browser),
    )

    db = SessionLocal()
    try:
        db.add(ev)
        db.commit()
        return jsonify({"ok": True}), 201
    except Exception:
        db.rollback()
        return jsonify({"ok": False}), 202
    finally:
        db.close()


def init_analytics(app, engine):
    global SessionLocal
    SessionLocal = sessionmaker(bind=engine)
    # Ensure table exists; restrict to AnalyticsEvent
    Base.metadata.create_all(bind=engine, tables=[AnalyticsEvent.__table__])
    app.register_blueprint(analytics_bp)

