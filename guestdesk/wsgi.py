"""WSGI entrypoint for production servers (gunicorn, uwsgi, etc.)."""

from .app import create_app

app = create_app()
