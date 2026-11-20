"""Vercel WSGI entrypoint — re-exports the Flask app from backend/app.py."""
from __future__ import annotations

import traceback

try:
    from backend.app import app  # noqa: F401
except Exception:  # pragma: no cover - surfaced on Vercel if import fails
    try:
        from flask import Flask
    except Exception:
        # Last resort: no Flask installed — raise so logs show the original error.
        raise RuntimeError(
            "Flask failed to import. Check pyproject.toml / requirements.txt dependencies.\n\n"
            + traceback.format_exc()
        ) from None

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
