"""Example MCP server to test mcp-hmr functionality."""

from mcp_hmr import begin_module_declare, finalize_module_declare, hmr_resource, hmr_tool

# Begin module declaration (for cleanup tracking)
_gen = begin_module_declare()


@hmr_tool()
def add_numbers(x: int, y: int) -> int:
    """Add two numbers together."""
    return x + y


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
    if uppercase:
        return text.upper()
    return text.lower()


@hmr_resource(uri_template="file://{path}")
def read_file(path: str) -> str:
    """Read contents of a file."""
    try:
        with open(path) as f:  # noqa: PTH123
            return f.read()
    except FileNotFoundError:
        return f"File not found: {path}"


def create_server():
    """Create and return an MCP server (mock implementation)."""
    print("MCP Server created with tools and resources:")
    print("Tools: add_numbers, process_text")
    print("Resources: read_file")
    print("Server is running... (press Ctrl+C to stop)")
    
    # In a real implementation, this would return an actual MCP server
    # For demo purposes, we'll just simulate a running server
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nServer stopped.")


# Finalize module declaration (for cleanup)
finalize_module_declare(_gen)


if __name__ == "__main__":
    create_server()