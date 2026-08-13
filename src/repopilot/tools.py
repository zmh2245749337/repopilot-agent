"""Repository tools shared by the HTTP application and the MCP server."""
from __future__ import annotations

from .core import RepoPilot, Tool, ToolRegistry, ToolResult


def create_registry(pilot: RepoPilot) -> ToolRegistry:
    """Register only bounded repository operations.

    Applying a patch is deliberately excluded: that action belongs to the task
    approval API, where it has a task state and an isolated workspace.
    """
    registry = ToolRegistry()
    registry.register(Tool("search_code", "low", lambda query, top_k=5: ToolResult(
        True, "search_completed", __import__("json").dumps(pilot.search_code(query, top_k), ensure_ascii=False))))
    registry.register(Tool("read_file", "low", pilot.read_file))
    registry.register(Tool("run_tests", "medium", pilot.run_pytest))
    return registry
