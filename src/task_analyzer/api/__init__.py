"""API package — FastAPI server for VS Code extension and future UIs."""

from task_analyzer.api.server import app, start_server

__all__ = ["app", "start_server"]
