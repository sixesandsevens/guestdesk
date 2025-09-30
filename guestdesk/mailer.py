"""Email delivery utilities and notification routing helpers."""

# GuestDesk
# Copyright (c) 2025 Chris Tanton
# SPDX-License-Identifier: LicenseRef-GDCL-1.1
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Iterable, Union, Optional, Dict, Any
from flask import current_app
from flask_babel import gettext as _

try:
    from .task_queue import q
except Exception:  # pragma: no cover
    q = None  # type: ignore

def _env_bool(name: str, default: str = "0") -> bool:
    """Interpret common truthy values from the environment."""
    return (os.getenv(name, default) or "").strip() in ("1", "true", "True", "yes", "on")

def _smtp_settings():
    """Return a dict of SMTP settings honoring both MAIL_* and SMTP_* envs."""
    host = os.getenv("MAIL_SERVER") or os.getenv("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.getenv("MAIL_PORT") or os.getenv("SMTP_PORT") or "587")
    user = os.getenv("MAIL_USERNAME") or os.getenv("SMTP_USERNAME")
    pwd  = os.getenv("MAIL_PASSWORD") or os.getenv("SMTP_PASSWORD")
    # TLS/SSL flags (both naming schemes supported)
    use_tls = _env_bool("MAIL_USE_TLS", os.getenv("SMTP_USE_TLS", "1"))
    use_ssl = _env_bool("MAIL_USE_SSL", os.getenv("SMTP_USE_SSL", "0"))
    # Sender preference
    sender = (
        os.getenv("EMAIL_FROM")
        or os.getenv("MAIL_DEFAULT_SENDER")
        or (user or "guestdesk@localhost")
    )
    # Allow global enable/disable via either name
    enabled = _env_bool("EMAIL_ENABLED", "1") and _env_bool("MAIL_ENABLED", "1")
    return {
        "host": host,
        "port": port,
        "user": user,
        "pwd": pwd,
        "use_tls": use_tls,
        "use_ssl": use_ssl,
        "sender": sender,
        "enabled": enabled,
    }

def _recipient_for(category: str) -> list[str]:
    """Pick recipients based on category. Returns a list of emails.

    Prefers list-based settings (e.g., MAINTENANCE_EMAIL_TO). Falls back to
    legacy single-address settings/envs if list is not present.
    """
    c = (category or "").strip().lower()
    cfg = getattr(current_app, "config", {})

    def _as_list(val, fallback_key=None):
        """Normalize comma-separated strings or lists into a list of addresses."""
        if isinstance(val, (list, tuple)):
            return [x for x in val if x]
        if isinstance(val, str) and ',' in val:
            return [x.strip() for x in val.split(',') if x.strip()]
        if isinstance(val, str) and val.strip():
            return [val.strip()]
        if fallback_key:
            fv = cfg.get(fallback_key) or os.getenv(fallback_key, '')
            return _as_list(fv)
        return []

    if c in ("maintenance", "maintenance issue", "maintenance issues"):
        return _as_list(cfg.get("MAINTENANCE_EMAIL_TO"), fallback_key="ADMIN_EMAIL")
    if c in ("grievance", "file a grievance"):
        return _as_list(cfg.get("GRIEVANCE_EMAIL_TO"), fallback_key="GRIEVANCE_EMAIL")
    if c in ("suggestion", "suggestions", "idea", "ideas", "suggestion/ideas"):
        return _as_list(cfg.get("SUGGESTION_EMAIL_TO"), fallback_key="SUGGESTION_EMAIL")
    if c in ("question", "ask a question"):
        return _as_list(cfg.get("QUESTION_EMAIL_TO"), fallback_key="QUESTION_EMAIL")
    return _as_list(cfg.get("MAINTENANCE_EMAIL_TO"), fallback_key="ADMIN_EMAIL")


def send_mail(subject: str,
              body: str,
              to: Union[str, Iterable[str]],
              reply_to: Optional[str] = None,
              sender: Optional[str] = None,
              cc: Optional[Iterable[str]] = None,
              attachments: Optional[Iterable[tuple]] = None) -> None:
    """Send an email via SMTP using flexible env config.

    - Honors EMAIL_ENABLED/MAIL_ENABLED flags (both must be truthy to send).
    - Supports MAIL_* and SMTP_* variable names.
    - Attempts unauthenticated delivery if no credentials set (local MTA).
    """
    if isinstance(to, str):
        to = [to]

    cfg = _smtp_settings()
    if not cfg["enabled"]:
        # Silently skip if disabled
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender or cfg["sender"]
    msg["To"] = ", ".join(to)
    if cc:
        # Normalize and set Cc header
        cc_list = [c for c in cc if c]
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body or "")

    # Attachments: list of (mime_type, filename, bytes)
    if attachments:
        for att in attachments:
            try:
                mime, fname, data = att
            except Exception:
                continue
            maintype, subtype = (mime.split("/", 1) + ["octet-stream"])[:2]
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=fname)

    # Connect and send
    if cfg["use_ssl"]:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20) as s:
            if cfg["user"] and cfg["pwd"]:
                s.login(cfg["user"], cfg["pwd"])
            s.send_message(msg)
    else:
        context = ssl.create_default_context()
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
            s.ehlo()
            if cfg["use_tls"]:
                s.starttls(context=context)
                s.ehlo()
            if cfg["user"] and cfg["pwd"]:
                s.login(cfg["user"], cfg["pwd"])
            s.send_message(msg)


def _deliver_mail_job(subject: str,
                      body: str,
                      to: list[str],
                      reply_to: Optional[str],
                      sender: Optional[str],
                      cc: Optional[list[str]],
                      attachments: Optional[Iterable[tuple]]) -> None:
    """Background job entry point used when RQ is available."""
    send_mail(
        subject=subject,
        body=body,
        to=to,
        reply_to=reply_to,
        sender=sender,
        cc=cc,
        attachments=attachments,
    )


def queue_mail(subject: str,
               body: str,
               to: Union[str, Iterable[str]],
               reply_to: Optional[str] = None,
               sender: Optional[str] = None,
               cc: Optional[Iterable[str]] = None,
               attachments: Optional[Iterable[tuple]] = None,
               job_timeout: int = 120) -> None:
    """Schedule an email for delivery or send immediately when queuing fails."""
    if isinstance(to, str):
        to_list = [to]
    else:
        to_list = [addr for addr in to if addr]
    cc_list = [c for c in (cc or []) if c]
    if q is None:
        send_mail(
            subject=subject,
            body=body,
            to=to_list,
            reply_to=reply_to,
            sender=sender,
            cc=cc_list,
            attachments=attachments,
        )
        return
    try:
        q.enqueue(
            _deliver_mail_job,
            subject,
            body,
            to_list,
            reply_to,
            sender,
            cc_list,
            attachments,
            job_timeout=job_timeout,
        )
    except Exception:
        send_mail(
            subject=subject,
            body=body,
            to=to_list,
            reply_to=reply_to,
            sender=sender,
            cc=cc_list,
            attachments=attachments,
        )


def send_category_notification(category: str,
                               payload_or_subject: Union[Dict[str, Any], str],
                               body: Optional[str] = None,
                               reply_to: Optional[str] = None) -> None:
    """Send a category-routed notification.

    - If the second arg is a dict, it may include: name, email, phone, subject, message, url, extra.
      In this mode, subject/body are constructed from the payload.
    - If the second arg is a string, it is used as subject and `body` must be provided.
    """
    to_list = _recipient_for(category)

    if isinstance(payload_or_subject, dict):
        payload = payload_or_subject
        cat_key = (category or '').lower()
        cat_labels = {
            'maintenance': _('Maintenance Issue'),
            'grievance': _('Grievance'),
            'suggestion': _('Suggestion / Idea'),
            'question': _('Question'),
        }
        category_label = cat_labels.get(cat_key, category.title())
        subj = (payload.get("subject") or _('New %(category)s submission', category=category_label)).strip()
        lines = [ _('Category: %(category)s', category=category_label) ]
        if payload.get("name"):   lines.append(_('Name: %(value)s', value=payload['name']))
        if payload.get("email"):  lines.append(_('Email: %(value)s', value=payload['email']))
        if payload.get("phone"):  lines.append(_('Phone: %(value)s', value=payload['phone']))
        if payload.get("url"):    lines.append(_('Page URL: %(value)s', value=payload['url']))
        if payload.get("extra"):  lines.append(_('Extra: %(value)s', value=payload['extra']))
        msg_text = payload.get("message")
        if msg_text:
            lines.append("")
            lines.append(_('Message:'))
            lines.append(str(msg_text))
        body_text = "\n\n".join([line for line in lines if line is not None])
        queue_mail(subject=subj, body=body_text, to=to_list, reply_to=reply_to or payload.get("email"))
        return

    # subject/body mode
    subject = str(payload_or_subject)
    queue_mail(subject=subject, body=body or "", to=to_list, reply_to=reply_to)
