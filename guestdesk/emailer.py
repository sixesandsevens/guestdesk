import os, smtplib, ssl, socket
from email.message import EmailMessage
from typing import Iterable, Tuple


def _env_b(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip() in ("1", "true", "TRUE", "yes", "on")


def _recipients() -> list[str]:
    raw = os.getenv("ISSUE_RECIPIENTS", "")
    return [e.strip() for e in raw.split(",") if e.strip()]


def send_mail(subject: str, body: str, to: Iterable[str]) -> Tuple[bool, str]:
    if not to:
        return False, "No recipients"
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USERNAME", "")
    pwd = os.getenv("SMTP_PASSWORD", "")
    use_tls = _env_b("SMTP_USE_TLS", "1")
    use_ssl = _env_b("SMTP_USE_SSL", "0")
    from_addr = os.getenv("EMAIL_FROM", user or "guestdesk@localhost")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)
    msg.set_content(body)

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=20) as s:
                if user:
                    s.login(user, pwd)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo()
                if use_tls:
                    s.starttls(context=ssl.create_default_context())
                if user:
                    s.login(user, pwd)
                s.send_message(msg)
        return True, "ok"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def send_issue_notification(issue: dict) -> Tuple[bool, str]:
    """Send a basic notification email for a new issue.

    `issue` is a dict built from the submission model and/or form fields.
    """
    if not _env_b("EMAIL_ENABLED", "1"):
        return False, "EMAIL_ENABLED not set/false"

    to = _recipients()
    host = socket.gethostname()
    subject = f"[GuestDesk] New issue: {issue.get('category') or issue.get('type') or 'General'}"
    lines = []
    for k in (
        "id",
        "category",
        "type",
        "title",
        "summary",
        "description",
        "details",
        "name",
        "email",
        "phone",
        "priority",
        "status",
        "created",
        "created_at",
        "location",
    ):
        if issue.get(k) not in (None, ""):
            lines.append(f"{k}: {issue[k]}")
    if url := issue.get("admin_url"):
        lines.append(f"\nAdmin: {url}")
    lines.append(f"\nServer: {host}")
    body = "\n".join(lines) if lines else repr(issue)

    return send_mail(subject, body, to)

