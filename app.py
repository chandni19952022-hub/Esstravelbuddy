"""Vercel entrypoint. Loads the Flask app from backend/app.py."""
from __future__ import annotations

import traceback

try:
    from backend.app import app  # noqa: F401
except Exception:  # pragma: no cover - shown on Vercel if import fails
    from flask import Flask

    app = Flask(__name__)
    _error = traceback.format_exc()

    @app.get("/")
    @app.get("/api/health")
    @app.get("/<path:path>")
    def _import_failed(path: str = ""):
        return (
            "<h1>Backend import failed on Vercel</h1>"
            f"<pre style='white-space:pre-wrap'>{_error}</pre>",
            500,
            {"content-type": "text/html; charset=utf-8"},
        )
