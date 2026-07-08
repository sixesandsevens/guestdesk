"""Database models that back the GuestDesk application."""

from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import declarative_base, relationship, backref
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Float, func, UniqueConstraint, Index



Base = declarative_base()


class Service(Base):
    """A public-facing service entry with localized content and schedule metadata."""
    __tablename__ = 'services'
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    name_en = Column(String(120), nullable=True)
    name_es = Column(String(120), nullable=True)
    category = Column(String(64), nullable=False) # Food, Showers, Laundry, Mail, ID, Medical, Legal, Employment, Transport, Other
    # Availability mode: 'scheduled' | 'on_call' | 'by_appt' | 'hotline'
    availability = Column(String(20), nullable=False, default='scheduled')
    # Off-site / phone-only / mobile unit flag
    is_offsite = Column(Boolean, nullable=False, default=False)
    description = Column(Text, nullable=True)
    description_en = Column(Text, nullable=True)
    description_es = Column(Text, nullable=True)
    location = Column(String(120), nullable=True)
    location_en = Column(String(120), nullable=True)
    location_es = Column(String(120), nullable=True)
    contact = Column(String(120), nullable=True)
    contact_en = Column(String(120), nullable=True)
    contact_es = Column(String(120), nullable=True)
    schedule_note = Column(String(200), nullable=True)
    schedule_note_en = Column(String(200), nullable=True)
    schedule_note_es = Column(String(200), nullable=True)
    external_link = Column(String(200), nullable=True)

    def _pick_locale(self, value_en: str | None, value_es: str | None, fallback: str | None = None) -> str | None:
        """Return the best localized value for the current visitor locale."""
        from flask_babel import get_locale
        locale = str(get_locale() or 'en').lower()
        if locale.startswith('es') and value_es:
            return value_es
        if locale.startswith('en') and value_en:
            return value_en
        # fallback: prefer English, then raw
        return value_en or fallback or value_es

    @property
    def name_i18n(self) -> str:
        """Return the service name in the visitor's preferred language."""
        return self._pick_locale(self.name_en, self.name_es, self.name) or ''

    @property
    def description_i18n(self) -> str:
        """Return the localized description when available."""
        return self._pick_locale(self.description_en, self.description_es, self.description) or ''

    @property
    def location_i18n(self) -> str:
        """Return the location string matching the visitor's locale."""
        return self._pick_locale(self.location_en, self.location_es, self.location) or ''

    @property
    def contact_i18n(self) -> str:
        """Return the localized contact details for the service."""
        return self._pick_locale(self.contact_en, self.contact_es, self.contact) or ''

    @property
    def schedule_note_i18n(self) -> str:
        """Return the localized schedule note for the service."""
        return self._pick_locale(self.schedule_note_en, self.schedule_note_es, self.schedule_note) or ''

class Announcement(Base):
    """Time-bound announcement displayed on the guest portal."""
    __tablename__ = 'announcements'
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    starts_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ends_at = Column(DateTime, nullable=True)

class Submission(Base):
    """Feedback or request submitted through the public forms."""
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
    """Administrative user account with role-based permissions."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), nullable=False, default="viewer")  # viewer|editor|admin
    created_at = Column(DateTime, default=datetime.utcnow)
    approved = Column(Boolean, nullable=False, default=True)


class UserPermission(Base):
    """Single granted permission key for a user (checkbox permission model).

    Admin-role users bypass permission checks entirely; rows here are the
    explicit grants for everyone else.
    """
    __tablename__ = "user_permissions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), nullable=False, index=True)
    permission = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", backref=backref("permissions", cascade="all, delete-orphan"))
    __table_args__ = (
        UniqueConstraint('user_id', 'permission', name='uix_user_permission'),
    )


class UserContact(Base):
    """Optional staff contact information tied to a user account."""
    __tablename__ = "user_contacts"
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), primary_key=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", backref=backref("contact", uselist=False, cascade="all, delete-orphan"))


class PasswordResetToken(Base):
    """Short-lived password reset token issued to staff accounts."""
    __tablename__ = "password_reset_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    request_ip = Column(String(64), nullable=True)
    user_agent = Column(Text, nullable=True)

    user = relationship("User")


class GameScore(Base):
    """High score entry for Fun Zone games."""
    __tablename__ = 'game_scores'
    id = Column(Integer, primary_key=True)
    game = Column(String(32), nullable=False, index=True)  # e.g., 'snake', 'tetris'
    name = Column(String(40), nullable=False, default='Anonymous')
    score = Column(Integer, nullable=False, default=0, index=True)
    meta = Column(Text, nullable=True)  # optional JSON blob (level, duration, etc.)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AnalyticsEvent(Base):
    """Stored analytics event emitted by the optional front-end tracker."""
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


Index('ix_analytics_events_started_at', AnalyticsEvent.started_at)
Index('ix_analytics_events_path_started', AnalyticsEvent.path, AnalyticsEvent.started_at)
Index('ix_analytics_events_anon_started', AnalyticsEvent.anon_id, AnalyticsEvent.started_at)
Index('ix_analytics_events_category_started', AnalyticsEvent.category, AnalyticsEvent.started_at)
Index('ix_analytics_events_is_staff_started', AnalyticsEvent.is_staff, AnalyticsEvent.started_at)


# ---- Recurring Service Schedules ----
class ServiceSeries(Base):
    """Recurring schedule definition (RRULE + overrides) for a service."""
    __tablename__ = "service_series"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    location = Column(String(200), nullable=True)
    category = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    tz = Column(String(64), nullable=True, default="America/New_York")

    # Link to owning service (optional)
    service_id = Column(Integer, ForeignKey('services.id', ondelete="CASCADE"), nullable=True, index=True)
    service = relationship("Service", backref=backref("series", cascade="all, delete-orphan"))

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
    """One-off adjustments or cancellations applied to slot/series instances."""
    __tablename__ = "service_overrides"

    id = Column(Integer, primary_key=True)
    series_id = Column(Integer, ForeignKey("service_series.id", ondelete="CASCADE"), index=True, nullable=True)
    # Direct override for a baseline slot occurrence (legacy fallback)
    service_id = Column(Integer, ForeignKey('services.id', ondelete="CASCADE"), index=True, nullable=True)
    # Original instance start (local time) this override targets
    instance_start = Column(DateTime, nullable=False)

    new_title = Column(String(200), nullable=True)
    new_location = Column(String(200), nullable=True)
    new_dtstart = Column(DateTime, nullable=True)
    new_dtend = Column(DateTime, nullable=True)
    cancelled = Column(Boolean, nullable=False, default=False)

ServiceSeries.overrides = relationship("ServiceOverride", backref="series", cascade="all, delete-orphan")

# ---- App Settings (key/value store) ----
class Setting(Base):
    """Simple key/value store for feature toggles and runtime options."""
    __tablename__ = 'settings'
    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=True)


# ---- PDF Template system ----
class PDFTemplate(Base):
    """Editable PDF template along with layout hints for overlay rendering."""
    __tablename__ = 'pdf_templates'

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    slug = Column(String(200), nullable=False, unique=True, index=True)
    file_path = Column(Text, nullable=True)  # where the uploaded PDF lives
    page_width_pt = Column(Integer, nullable=True)
    page_height_pt = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, default='draft')  # draft|published|archived
    # Draft working layout (normalized JSON string)
    draft_layout_json = Column(Text, nullable=True)
    # Template-level baseline padding (points)
    baseline_pad_pt = Column(Float, nullable=False, default=3.0)
    # Audit
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    versions = relationship("PDFTemplateVersion", back_populates="template", cascade="all, delete-orphan", order_by="PDFTemplateVersion.version.desc()")


class PDFTemplateVersion(Base):
    """Versioned snapshot of a PDF template's layout configuration."""
    __tablename__ = 'pdf_template_versions'

    id = Column(Integer, primary_key=True)
    template_id = Column(Integer, ForeignKey('pdf_templates.id', ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)  # increments per template
    normalized_layout_json = Column(Text, nullable=False)
    baseline_pad_pt = Column(Float, nullable=False, default=3.0)
    notes = Column(Text, nullable=True)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    template = relationship("PDFTemplate", back_populates="versions")
    __table_args__ = (
        UniqueConstraint('template_id', 'version', name='uix_template_version'),
    )


class PDFBinding(Base):
    """Link between a public form and a specific published PDF template version."""
    __tablename__ = 'pdf_bindings'

    id = Column(Integer, primary_key=True)
    form_key = Column(String(64), nullable=False, index=True)  # e.g., grievance, maintenance, question
    template_id = Column(Integer, ForeignKey('pdf_templates.id', ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)  # immutable binding to a published version
    is_active = Column(Boolean, nullable=False, default=True)
    field_map = Column(Text, nullable=True)  # optional JSON mapping overrides
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    template = relationship("PDFTemplate")
    __table_args__ = (
        Index('ix_pdf_bindings_active', 'form_key', 'is_active'),
    )


# ---- Grievance tracker ----
class GrievanceCase(Base):
    """Operational case record tracking a grievance from receipt to closure.

    Every grievance Submission gets exactly one case. The case carries the
    workflow state (assignment, status, due dates, findings, closure) plus the
    intake fields that are not persisted on the flat Submission row.
    """
    __tablename__ = 'grievance_cases'

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey('submissions.id', ondelete="CASCADE"), nullable=False, unique=True, index=True)
    public_reference = Column(String(64), nullable=False, unique=True, index=True)
    # Yearly grievance counter behind new-format references (GRV-<sid>-<year>-<seq>).
    # Null on pre-v0.3 cases, whose references keep the legacy timestamp format.
    grievance_year = Column(Integer, nullable=True, index=True)
    grievance_sequence = Column(Integer, nullable=True)
    # guest_digital | paper | verbal | staff_assisted
    source = Column(String(32), nullable=False, default='guest_digital')
    # When the grievance was originally received (may predate data entry for paper/verbal)
    original_received_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    entered_by_user_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True)
    assigned_reviewer_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True, index=True)
    # received | acknowledged | in_review | response_provided | additional_review | closed
    status = Column(String(32), nullable=False, default='received', index=True)

    # Intake fields captured from the grievance form (not stored on Submission)
    staff_involved = Column(String(200), nullable=True)
    involves_grace_staff = Column(Boolean, nullable=False, default=False)
    involves_policies = Column(Boolean, nullable=False, default=False)
    involves_volunteer = Column(Boolean, nullable=False, default=False)
    involves_other = Column(Boolean, nullable=False, default=False)
    involves_other_text = Column(String(200), nullable=True)
    # Kept as form-supplied strings (YYYY-MM-DD / HH:MM); display data, not filtered on
    incident_date = Column(String(10), nullable=True)
    incident_time = Column(String(8), nullable=True)
    intake_notes = Column(Text, nullable=True)  # private staff notes from intake, never guest-facing

    # Deadline tracking (business days from original_received_at)
    acknowledgement_due_at = Column(DateTime, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    response_due_at = Column(DateTime, nullable=True)
    response_provided_at = Column(DateTime, nullable=True)
    response_method = Column(String(64), nullable=True)

    # Outcome fields — findings/resolution are internal; guest_facing_response is
    # the only field whose content may be shared with the complainant
    findings = Column(Text, nullable=True)
    resolution = Column(Text, nullable=True)
    guest_facing_response = Column(Text, nullable=True)
    closure_notes = Column(Text, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    closed_by_user_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True)

    additional_review_requested_at = Column(DateTime, nullable=True)
    additional_review_due_at = Column(DateTime, nullable=True)
    additional_review_status = Column(String(32), nullable=True)

    # Soft delete: archived cases are hidden from the tracker but never removed
    archived_at = Column(DateTime, nullable=True, index=True)
    archived_by_user_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    submission = relationship("Submission", backref=backref("grievance_case", uselist=False, cascade="all, delete-orphan"))
    entered_by = relationship("User", foreign_keys=[entered_by_user_id])
    assigned_reviewer = relationship("User", foreign_keys=[assigned_reviewer_id])
    closed_by = relationship("User", foreign_keys=[closed_by_user_id])
    archived_by = relationship("User", foreign_keys=[archived_by_user_id])


class GrievanceAttachment(Base):
    """File attached to a grievance case (scanned originals, supporting docs)."""
    __tablename__ = 'grievance_attachments'

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey('grievance_cases.id', ondelete="CASCADE"), nullable=False, index=True)
    # original_handwritten_grievance | verbal_grievance_documentation |
    # supporting_documentation | photo | system_generated_pdf | other
    attachment_type = Column(String(48), nullable=False, default='supporting_documentation')
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False)
    storage_path = Column(Text, nullable=False)
    uploaded_by_user_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True)
    uploaded_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    case = relationship("GrievanceCase", backref=backref("attachments", cascade="all, delete-orphan", order_by="GrievanceAttachment.uploaded_at"))
    uploaded_by = relationship("User")


class GrievanceNote(Base):
    """Staff note attached to a grievance case."""
    __tablename__ = 'grievance_notes'

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey('grievance_cases.id', ondelete="CASCADE"), nullable=False, index=True)
    author_user_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True)
    author_label = Column(String(64), nullable=False, default='staff')
    # internal | investigation | guest_contact | supervisor_review | closure
    note_type = Column(String(32), nullable=False, default='internal')
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    case = relationship("GrievanceCase", backref=backref("notes", cascade="all, delete-orphan", order_by="GrievanceNote.created_at"))
    author = relationship("User")


class GrievanceEvent(Base):
    """Timeline entry recording an action taken on a grievance case."""
    __tablename__ = 'grievance_events'

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey('grievance_cases.id', ondelete="CASCADE"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True)
    # Stable display label ('guest', 'admin-session', username) — survives user deletion
    actor_label = Column(String(64), nullable=False, default='system')
    event_type = Column(String(48), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    meta_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    case = relationship("GrievanceCase", backref=backref("events", cascade="all, delete-orphan", order_by="GrievanceEvent.created_at"))
    actor = relationship("User")


# ---- Simplified per-form PDF config ----
class FormPDFConfig(Base):
    """Simplified per-form PDF binding used by the streamlined renderer."""
    __tablename__ = "form_pdf_config"

    id = Column(Integer, primary_key=True)
    form_key = Column(String(64), unique=True, index=True, nullable=False)
    # e.g. /opt/guestdesk/guestdesk/static/pdf/templates/<key>.pdf
    template_path = Column(String(512), nullable=True)
    # JSON string in simplified schema (bottom-left point coords)
    layout_json = Column(Text, nullable=True)
    baseline_pad = Column(Integer, nullable=False, default=3)
    attach_to_email = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
