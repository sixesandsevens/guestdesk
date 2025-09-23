"""Helpers for configuring PDF rendering directories and flags."""

import os


def pdf_render_enabled() -> bool:
    """Return ``True`` when PDF overlay rendering is enabled via env flag."""
    return (os.getenv("PDF_RENDER_ENABLED", "0") or "").strip() in ("1", "true", "True", "yes", "on")


def template_storage_root() -> str:
    """Return the directory where uploaded PDF templates are stored."""
    # Where uploaded PDF templates live
    return os.getenv("PDF_TEMPLATE_STORAGE_ROOT", "/opt/guestdesk/uploads/pdf-templates")


def output_root() -> str:
    """Return the directory used to persist rendered PDF artifacts."""
    # Where rendered PDFs are written for archival/attachments
    return os.getenv("PDF_OUTPUT_ROOT", "/var/lib/guestdesk/pdf")
