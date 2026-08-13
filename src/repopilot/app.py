"""Uvicorn entry point. Set REPOPILOT_REPO_PATH to choose the target repository."""
from __future__ import annotations

import os

from .api import create_app

app = create_app(os.environ.get("REPOPILOT_REPO_PATH", "."))
