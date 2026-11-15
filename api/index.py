"""Vercel entry point: serves the Flask API at /api from the same project as the frontend."""

import os
import sys

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

os.environ.setdefault("FLASK_ENV", "production")

from app import create_app  # noqa: E402

app = create_app()
