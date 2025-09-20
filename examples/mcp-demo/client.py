#!/usr/bin/env python3
"""
Simple MCP client for testing the demo server.
"""

import asyncio
import json
import subprocess
from typing import Any


class MCPClient:
    """A simple MCP client implementation."""

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.request_id = 0

    def get_next_id(self) -> int:
        """Get the next request ID."""
        self.request_id += 1
        return self.request_id

    async def start_server(self, command: list[str]):
        """Start the MCP server process."""
        print(f"🚀 Starting server: {' '.join(command)}")
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=0)  # noqa: ASYNC220

        # Wait a moment for the server to start
        await asyncio.sleep(0.5)

        if self.process.poll() is not None:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"Server failed to start. Error: {stderr}")

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a request to the MCP server."""
        if not self.process:
            raise RuntimeError("Server not started")

        request = {"jsonrpc": "2.0", "id": self.get_next_id(), "method": method}

        if params:
            request["params"] = params

        # Send request
        request_json = json.dumps(request) + "\n"
        self.process.stdin.write(request_json)
        self.process.stdin.flush()

        # Read response
        response_line = self.process.stdout.readline().strip()
        if not response_line:
            raise RuntimeError("No response from server")

        try:
            response = json.loads(response_line)
            return response
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid JSON response: {e}") from e

    async def stop_server(self):
        """Stop the MCP server."""
        if self.process:
            self.process.terminate()
            try:
                await asyncio.wait_for(asyncio.get_event_loop().run_in_executor(None, self.process.wait), timeout=5.0)
            except TimeoutError:
                self.process.kill()
            self.process = None


async def test_server():
    """Test the MCP server functionality."""
    client = MCPClient()

    try:
        # Start the server
        await client.start_server(["python3", "server.py"])

        print("\n🔧 Testing MCP server...")

        # Initialize
        print("\n1. Initializing server...")
        response = await client.send_request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test-client", "version": "0.1.0"}})

        if "error" in response:
            print(f"❌ Initialize failed: {response['error']}")
            return

        print(f"✅ Server initialized: {response['result']['serverInfo']['name']}")

        # List tools
        print("\n2. Listing available tools...")
        response = await client.send_request("tools/list")

        if "error" in response:
            print(f"❌ Tools list failed: {response['error']}")
            return

        tools = response["result"]["tools"]
        print(f"✅ Found {len(tools)} tools:")
        for tool in tools:
            print(f"   - {tool['name']}: {tool['description']}")

        # Test echo tool
        print("\n3. Testing echo tool...")
        response = await client.send_request("tools/call", {"name": "echo", "arguments": {"message": "Hello, MCP world!"}})

        if "error" in response:
            print(f"❌ Echo test failed: {response['error']}")
        else:
            content = response["result"]["content"]
            print(f"✅ Echo response: {content[0]['text']}")

        # Test calculate tool
        print("\n4. Testing calculate tool...")
        response = await client.send_request("tools/call", {"name": "calculate", "arguments": {"operation": "add", "a": 15, "b": 27}})

        if "error" in response:
            print(f"❌ Calculate test failed: {response['error']}")
        else:
            content = response["result"]["content"]
            print(f"✅ Calculate response: {content[0]['text']}")

        # Test time tool
        print("\n5. Testing time tool...")
        response = await client.send_request("tools/call", {"name": "time", "arguments": {"format": "readable"}})

        if "error" in response:
            print(f"❌ Time test failed: {response['error']}")
        else:
            content = response["result"]["content"]
            print(f"✅ Time response: {content[0]['text']}")

        print("\n🎉 All tests completed successfully!")

    except Exception as e:
        print(f"❌ Test failed: {e}")
    finally:
        await client.stop_server()


if __name__ == "__main__":
    print("🧪 MCP Server Test Client")
    print("=" * 50)
    asyncio.run(test_server())
