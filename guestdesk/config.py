import os


class Config:
    """App configuration sourced from environment.

    Systemd provides env vars (e.g., from /opt/guestdesk/.env).
    """

    # Mail settings (Flask-Mail compatible)
    MAIL_SERVER = os.getenv("MAIL_SERVER", os.getenv("SMTP_HOST", "smtp.gmail.com"))
    MAIL_PORT = int(os.getenv("MAIL_PORT", os.getenv("SMTP_PORT", "587")))
    MAIL_USE_TLS = (os.getenv("MAIL_USE_TLS", os.getenv("SMTP_USE_TLS", "1")) in ("1", "true", "True"))
    MAIL_USE_SSL = (os.getenv("MAIL_USE_SSL", os.getenv("SMTP_USE_SSL", "0")) in ("1", "true", "True"))
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", os.getenv("SMTP_USERNAME"))
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", os.getenv("SMTP_PASSWORD"))
    # Prefer a named sender; Flask-Mail accepts (name, email)
    MAIL_DEFAULT_SENDER = (
        os.getenv("MAIL_SENDER_NAME", "GuestDesk Notifications"),
        os.getenv(
            "MAIL_DEFAULT_SENDER",
            os.getenv(
                "EMAIL_FROM", os.getenv("MAIL_USERNAME", os.getenv("SMTP_USERNAME", "guestdesk@localhost"))
            ),
        ),
    )

    # Toggle email notifications (kept for compatibility)
    EMAIL_ENABLED = (os.getenv("EMAIL_ENABLED", "1") in ("1", "true", "True")) and (
        os.getenv("MAIL_ENABLED", "1") in ("1", "true", "True")
    )

    # Recipient defaults for categories (override via env)
    # Legacy single-address envs are still honored as fallbacks
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@gracemarketplace.org")
    GRIEVANCE_EMAIL = os.getenv("GRIEVANCE_EMAIL", "jkupfer@gracemarketplace.org")
    SUGGESTION_EMAIL = os.getenv("SUGGESTION_EMAIL", "jkupfer@gracemarketplace.org")
    QUESTION_EMAIL = os.getenv("QUESTION_EMAIL", "jkupfer@gracemarketplace.org")

    # List-based recipient settings (comma-separated in env)
    MAINTENANCE_EMAIL_TO = [x.strip() for x in (os.getenv("MAINTENANCE_EMAIL_TO") or ADMIN_EMAIL).split(',') if x.strip()]
    SUGGESTION_EMAIL_TO = [x.strip() for x in (os.getenv("SUGGESTION_EMAIL_TO") or SUGGESTION_EMAIL).split(',') if x.strip()]
    QUESTION_EMAIL_TO   = [x.strip() for x in (os.getenv("QUESTION_EMAIL_TO")   or QUESTION_EMAIL).split(',') if x.strip()]

    # Static asset version for cache-busting (optional)
    ASSET_VERSION = os.getenv("ASSET_VERSION", "1")

    # --- Grievance PDF settings ---
    GRIEVANCE_TEMPLATE_PDF = os.getenv(
        "GRIEVANCE_TEMPLATE_PDF",
        "/opt/guestdesk/guestdesk/static/pdf/Grievance_template.pdf",
    )
    GRIEVANCE_ARCHIVE_DIR = os.getenv(
        "GRIEVANCE_ARCHIVE_DIR",
        "/opt/guestdesk/forms/grievances",
    )
    GRIEVANCE_EMAIL_TO = (
        os.getenv(
            "GRIEVANCE_EMAIL_TO",
            "jkupfer@gracemarketplace.org,matt@example.org",
        ).split(",")
    )
    GRIEVANCE_EMAIL_CC = (
        os.getenv("GRIEVANCE_EMAIL_CC", "").split(",")
        if os.getenv("GRIEVANCE_EMAIL_CC")
        else []
    )
    GRIEVANCE_FROM = os.getenv("GRIEVANCE_FROM", "no-reply@guestdesk.local")
