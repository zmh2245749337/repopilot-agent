"""stdio MCP server exposing only bounded RepoPilot tools."""
from __future__ import annotations

import asyncio
import json

from .mcp_tools import TOOL_DEFINITIONS, read_file, run_tests, search_code


async def main() -> None:
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
    except ImportError as error:  # pragma: no cover - optional dependency
        raise RuntimeError("Install the MCP extra: pip install -e '.[mcp]'") from error

    server = Server("repopilot-mcp-server")

    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        return ListToolsResult(tools=[Tool(name=item["name"], description=item["description"], inputSchema=item["inputSchema"])
                                      for item in TOOL_DEFINITIONS])

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> CallToolResult:
        handlers = {"search_code": search_code, "read_file": read_file, "run_tests": run_tests}
        if name not in handlers:
            return CallToolResult(content=[TextContent(type="text", text=f"Unknown tool: {name}")], isError=True)
        try:
            result = handlers[name](**arguments)
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))])
        except Exception as error:
            return CallToolResult(content=[TextContent(type="text", text=str(error))], isError=True)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
