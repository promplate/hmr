"""
Simple MCP (Model Context Protocol) server demo.
This demonstrates a basic MCP server that can be used with mcp-hmr for hot reloading.
"""

import asyncio
import json
import sys
from typing import Any


class MCPServer:
    """A simple MCP server implementation."""

    def __init__(self, name: str = "demo-server", version: str = "0.1.0"):
        self.name = name
        self.version = version
        self.tools = {}
        self.resources = {}

    def add_tool(self, name: str, description: str, schema: dict[str, Any], handler):
        """Add a tool to the server."""
        self.tools[name] = {"name": name, "description": description, "inputSchema": schema, "handler": handler}

    def add_resource(self, uri: str, name: str, description: str, handler):
        """Add a resource to the server."""
        self.resources[uri] = {"uri": uri, "name": name, "description": description, "handler": handler}

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle an incoming MCP request."""
        method = request.get("method")
        params = request.get("params", {})
        request_id = request.get("id")

        try:
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {"listChanged": True},
                            "resources": {"subscribe": True, "listChanged": True},
                        },
                        "serverInfo": {"name": self.name, "version": self.version},
                    },
                }

            elif method == "tools/list":
                tools_list = []
                for _tool_name, tool_info in self.tools.items():
                    tools_list.append({"name": tool_info["name"], "description": tool_info["description"], "inputSchema": tool_info["inputSchema"]})

                return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools_list}}

            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})

                if tool_name not in self.tools:
                    raise ValueError(f"Unknown tool: {tool_name}")

                handler = self.tools[tool_name]["handler"]
                result = await handler(arguments)

                return {"jsonrpc": "2.0", "id": request_id, "result": {"content": result}}

            elif method == "resources/list":
                resources_list = []
                for _uri, resource_info in self.resources.items():
                    resources_list.append({"uri": resource_info["uri"], "name": resource_info["name"], "description": resource_info["description"]})

                return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": resources_list}}

            else:
                raise ValueError(f"Unknown method: {method}")

        except Exception as e:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(e)}}

    async def run_stdio(self):
        """Run the server using stdio transport."""
        print(f"🚀 Starting {self.name} v{self.version} with stdio transport", file=sys.stderr)
        print("📝 Server is ready and listening for requests...", file=sys.stderr)

        while True:
            try:
                # Read JSON-RPC request from stdin
                line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"❌ Invalid JSON: {e}", file=sys.stderr)
                    continue

                # Handle the request
                response = await self.handle_request(request)

                # Send response to stdout
                print(json.dumps(response), flush=True)

            except KeyboardInterrupt:
                print("\n👋 Server shutting down...", file=sys.stderr)
                break
            except Exception as e:
                print(f"❌ Server error: {e}", file=sys.stderr)


# Create server instance
server = MCPServer("demo-server", "0.1.0")


# Tool handlers
async def echo_handler(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Echo back the input message."""
    message = args.get("message", "")
    return [{"type": "text", "text": f"🔊 Echo: {message}"}]


async def calculate_handler(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Perform basic calculations."""
    operation = args.get("operation", "add")
    a = args.get("a", 0)
    b = args.get("b", 0)

    try:
        if operation == "add":
            result = a + b
        elif operation == "subtract":
            result = a - b
        elif operation == "multiply":
            result = a * b
        elif operation == "divide":
            if b == 0:
                raise ValueError("Division by zero")
            result = a / b
        else:
            raise ValueError(f"Unknown operation: {operation}")

        return [{"type": "text", "text": f"🧮 Calculation: {a} {operation} {b} = {result}"}]
    except Exception as e:
        return [{"type": "text", "text": f"❌ Error: {e!s}"}]


async def time_handler(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Get current time information."""
    import datetime

    now = datetime.datetime.now()

    format_type = args.get("format", "iso")

    if format_type == "iso":
        time_str = now.isoformat()
    elif format_type == "readable":
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    elif format_type == "timestamp":
        time_str = str(int(now.timestamp()))
    else:
        time_str = str(now)

    return [{"type": "text", "text": f"🕐 Current time ({format_type}): {time_str}"}]


# Register tools
server.add_tool(
    "echo", "Echo back the provided message", {"type": "object", "properties": {"message": {"type": "string", "description": "The message to echo back"}}, "required": ["message"]}, echo_handler
)

server.add_tool(
    "calculate",
    "Perform basic mathematical calculations",
    {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["add", "subtract", "multiply", "divide"], "description": "The mathematical operation to perform"},
            "a": {"type": "number", "description": "First number"},
            "b": {"type": "number", "description": "Second number"},
        },
        "required": ["operation", "a", "b"],
    },
    calculate_handler,
)

server.add_tool(
    "time",
    "Get current time in various formats",
    {"type": "object", "properties": {"format": {"type": "string", "enum": ["iso", "readable", "timestamp"], "description": "The format for the time output", "default": "iso"}}},
    time_handler,
)


async def main():
    """Main entry point for the MCP server."""
    await server.run_stdio()


if __name__ == "__main__":
    asyncio.run(main())
