from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, func



Base = declarative_base()

class Service(Base):
    __tablename__ = 'services'
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    category = Column(String(64), nullable=False) # Food, Showers, Laundry, Mail, ID, Medical, Legal, Employment, Transport, Other
    # Availability mode: 'scheduled' | 'on_call' | 'by_appt' | 'hotline'
    availability = Column(String(20), nullable=False, default='scheduled')
    # Off-site / phone-only / mobile unit flag
    is_offsite = Column(Boolean, nullable=False, default=False)
    description = Column(Text, nullable=True)
    location = Column(String(120), nullable=True)
    contact = Column(String(120), nullable=True)
    schedule_note = Column(String(200), nullable=True)
    external_link = Column(String(200), nullable=True)
    slots = relationship("ProgramSlot", back_populates="service", cascade="all, delete-orphan", order_by="ProgramSlot.dow")

class ProgramSlot(Base):
    __tablename__ = 'program_slots'
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey('services.id', ondelete="CASCADE"), index=True, nullable=False)
    dow = Column(Integer, nullable=False) # 0=Mon...6=Sun
    start = Column(String(5), nullable=True) # "09:00"
    end = Column(String(5), nullable=True)   # "11:30"
    note = Column(String(200), nullable=True)

    service = relationship("Service", back_populates="slots")

class Announcement(Base):
    __tablename__ = 'announcements'
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    starts_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ends_at = Column(DateTime, nullable=True)

class Submission(Base):
    __tablename__ = 'submissions'
    id = Column(Integer, primary_key=True)
    kind = Column(String(32), nullable=False) # maintenance, grievance, suggestion, question
    subject = Column(String(200), nullable=True)
    body = Column(Text, nullable=False)
    category = Column(String(64), nullable=True) # for maintenance type
    building = Column(String(120), nullable=True)
    location = Column(String(120), nullable=True)
    contact_name = Column(String(120), nullable=True)
    contact_info = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    status = Column(String(16), nullable=False, default='new')

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), nullable=False, default="viewer")  # viewer|editor|admin
    created_at = Column(DateTime, default=datetime.utcnow)
    approved = Column(Boolean, nullable=False, default=True)


class GameScore(Base):
    __tablename__ = 'game_scores'
    id = Column(Integer, primary_key=True)
    game = Column(String(32), nullable=False, index=True)  # e.g., 'snake', 'tetris'
    name = Column(String(40), nullable=False, default='Anonymous')
    score = Column(Integer, nullable=False, default=0, index=True)
    meta = Column(Text, nullable=True)  # optional JSON blob (level, duration, etc.)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id = Column(Integer, primary_key=True)
    client_id = Column(String(64), index=True, nullable=True)
    session_id = Column(String(64), index=True, nullable=True)
    path = Column(Text, index=True, nullable=False)
    referrer = Column(Text, nullable=True)

    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=False)
    duration_ms = Column(Integer, nullable=False, default=0)

    ip_hash = Column(String(32), index=True, nullable=True)
    user_agent = Column(Text, nullable=True)
    device = Column(String(32), index=True)
    os = Column(String(64), index=True)
    browser = Column(String(64), index=True)

    # Extended metrics (nullable for back-compat)
    category = Column(String(32), nullable=True, index=True)   # page|form|funzone|admin
    action = Column(String(32), nullable=True)                 # view|submit|play|...
    label = Column(String(128), nullable=True)                 # e.g., maintenance, printer_jam
    referrer_path = Column(Text, nullable=True)
    is_staff = Column(Boolean, nullable=True, default=False, index=True)
    page_load_ms = Column(Integer, nullable=True)
    anon_id = Column(String(64), nullable=True, index=True)

    created_at = Column(DateTime, nullable=False, server_default=func.now())


# ---- Recurring Service Schedules ----
class ServiceSeries(Base):
    __tablename__ = "service_series"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    location = Column(String(200), nullable=True)
    category = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    tz = Column(String(64), nullable=True, default="America/New_York")

    # Link to owning service (optional)
    service_id = Column(Integer, ForeignKey('services.id', ondelete="CASCADE"), nullable=True, index=True)

    # Base instance times (local time)
    dtstart = Column(DateTime, nullable=False)
    dtend = Column(DateTime, nullable=False)

    # Recurrence: RFC 5545 rule text + explicit include/exclude lists
    rrule = Column(Text, nullable=True)
    # Store as JSON string in TEXT for broad DB compatibility
    rdate = Column(Text, nullable=True)   # JSON array string of ISO datetimes
    exdate = Column(Text, nullable=True)  # JSON array string of ISO dates/datetimes

    is_all_day = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
class ServiceOverride(Base):
    __tablename__ = "service_overrides"

    id = Column(Integer, primary_key=True)
    series_id = Column(Integer, ForeignKey("service_series.id", ondelete="CASCADE"), index=True, nullable=True)
    # Direct override for a baseline slot occurrence (no series)
    service_id = Column(Integer, ForeignKey('services.id', ondelete="CASCADE"), index=True, nullable=True)
    # Original instance start (local time) this override targets
    instance_start = Column(DateTime, nullable=False)

    new_title = Column(String(200), nullable=True)
    new_location = Column(String(200), nullable=True)
    new_dtstart = Column(DateTime, nullable=True)
    new_dtend = Column(DateTime, nullable=True)
    cancelled = Column(Boolean, nullable=False, default=False)

from sqlalchemy.orm import relationship
ServiceSeries.overrides = relationship("ServiceOverride", backref="series", cascade="all, delete-orphan")
