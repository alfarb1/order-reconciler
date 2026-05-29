"""Local-only Flask UI for resolving missing/pending/orphan shipments.

Binds to 127.0.0.1 only. No auth — assumes the user is the only one on the
machine. All state writes go through the same SQLite DB the CLI uses.
"""
from __future__ import annotations

from .app import create_app, run_server

__all__ = ["create_app", "run_server"]
