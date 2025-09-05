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
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@gracemarketplace.org")
    GRIEVANCE_EMAIL = os.getenv("GRIEVANCE_EMAIL", "jkupfer@gracemarketplace.org")
    SUGGESTION_EMAIL = os.getenv("SUGGESTION_EMAIL", "jkupfer@gracemarketplace.org")
    QUESTION_EMAIL = os.getenv("QUESTION_EMAIL", "jkupfer@gracemarketplace.org")

    # Static asset version for cache-busting (optional)
    ASSET_VERSION = os.getenv("ASSET_VERSION", "1")
