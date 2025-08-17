"""Comprehensive test example for mcp-hmr functionality."""

import time

from mcp_hmr import begin_module_declare, finalize_module_declare, hmr_resource, hmr_tool

# Begin module declaration (for cleanup tracking)
_gen = begin_module_declare()

# Simulate some "expensive" state that should be preserved
EXPENSIVE_MODEL = {"loaded_at": time.time(), "model_data": "This represents a large ML model"}
DATABASE_POOL = {"connections": 5, "initialized_at": time.time()}


@hmr_tool()
def add_numbers(x: int, y: int) -> int:
    """Add two numbers together."""
    print(f"🔢 Adding {x} + {y}")
    return x + y


@hmr_tool()
def multiply_numbers(x: int, y: int) -> int:
    """Multiply two numbers together."""
    print(f"✖️ Multiplying {x} * {y}")
    return x * y


@hmr_tool(
    schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to process"},
            "uppercase": {"type": "boolean", "description": "Convert to uppercase", "default": False},
        },
        "required": ["text"],
    }
)
def process_text(text: str, uppercase: bool = False) -> str:  # noqa: FBT001, FBT002
    """Process text with optional uppercase conversion."""
    print(f"📝 Processing text: '{text}' (uppercase={uppercase})")
    if uppercase:
        return text.upper()
    return text.lower()


@hmr_tool()
def get_model_info() -> dict:
    """Get information about the loaded model (preserved across reloads)."""
    print(f"🧠 Model loaded at: {EXPENSIVE_MODEL['loaded_at']}")
    return EXPENSIVE_MODEL.copy()


@hmr_resource(uri_template="file://{path}")
def read_file(path: str) -> str:
    """Read contents of a file."""
    print(f"📂 Reading file: {path}")
    try:
        with open(path) as f:  # noqa: PTH123
            return f.read()
    except FileNotFoundError:
        return f"File not found: {path}"


@hmr_resource(uri_template="db://documents/{doc_id}")
def get_document(doc_id: str) -> dict:
    """Get a document from the database."""
    print(f"🗄️ Fetching document: {doc_id}")
    return {
        "id": doc_id,
        "content": f"This is document {doc_id}",
        "db_pool": DATABASE_POOL,
    }


def create_server():
    """Create and return an MCP server (mock implementation)."""
    print("🚀 MCP Server starting...")
    print("=" * 60)
    print("📋 Registered Tools:")

    from mcp_hmr.registry import get_registered_tools
    tools = get_registered_tools()
    for tool_name, tool_info in tools.items():
        schema = tool_info.get('schema') or {}
        description = schema.get('description', f"Tool: {tool_name}")
        print(f"  • {tool_name} - {description}")

    print("\n📚 Registered Resources:")
    from mcp_hmr.registry import get_registered_resources
    resources = get_registered_resources()
    for resource_name, resource_info in resources.items():
        template = resource_info.get('uri_template', 'No template')
        print(f"  • {resource_name} - {template}")

    print("\n💾 Preserved State:")
    print(f"  • Model loaded at: {EXPENSIVE_MODEL['loaded_at']}")
    print(f"  • DB connections: {DATABASE_POOL['connections']}")

    print("\n🔥 Hot reloading enabled! Edit this file to see changes...")
    print("=" * 60)
    print("Server is running... (press Ctrl+C to stop)")

    # In a real implementation, this would return an actual MCP server
    # For demo purposes, we'll just simulate a running server
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Server stopped.")


# Finalize module declaration (for cleanup)
finalize_module_declare(_gen)


if __name__ == "__main__":
    create_server()
