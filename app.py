"""Vercel Flask entrypoint — re-exports the app from backend/app.py."""
from backend.app import app

__all__ = ["app"]
