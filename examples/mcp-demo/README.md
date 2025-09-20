# MCP Demo Server

This is a demonstration MCP (Model Context Protocol) server that showcases hot module reloading with `mcp-hmr`.

## Features

The demo server implements several tools:

1. **Echo Tool** - Echoes back any message you send
2. **Calculate Tool** - Performs basic mathematical operations (add, subtract, multiply, divide)
3. **Time Tool** - Returns current time in various formats (ISO, readable, timestamp)

## Running the Demo

### Option 1: Regular Python (no hot reloading)

```bash
cd examples/mcp-demo
python server.py
```

### Option 2: With mcp-hmr (hot reloading enabled)

```bash
cd examples/mcp-demo
mcp-hmr server:main
```

### Testing the Server

You can test the server using the included test client:

```bash
python client.py
```

## Hot Reloading Demo

1. Start the server with mcp-hmr:
   ```bash
   mcp-hmr server:main --clear
   ```

2. In another terminal, run the test client:
   ```bash
   python client.py
   ```

3. While the server is running, try modifying the server code:
   - Change the echo message format in the `echo_handler` function
   - Add a new mathematical operation to the `calculate_handler`
   - Modify the time format options in the `time_handler`

4. Save the file and notice how the server automatically reloads without losing connection!

## Example Modifications to Try

### 1. Modify the Echo Handler

In `server.py`, find the `echo_handler` function and change:

```python
return [
    {
        "type": "text",
        "text": f"🔊 Echo: {message}"
    }
]
```

to:

```python
return [
    {
        "type": "text",
        "text": f"🎯 You said: {message.upper()}"
    }
]
```

### 2. Add a New Operation to Calculator

In the `calculate_handler` function, add a new operation:

```python
elif operation == "power":
    result = a ** b
```

And update the schema to include "power" in the enum.

### 3. Add a New Tool

Add a new tool that generates random numbers:

```python
async def random_handler(args: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate a random number."""
    import random
    min_val = args.get("min", 0)
    max_val = args.get("max", 100)
    
    number = random.randint(min_val, max_val)
    
    return [
        {
            "type": "text",
            "text": f"🎲 Random number between {min_val} and {max_val}: {number}"
        }
    ]

# Register the new tool
server.add_tool(
    "random",
    "Generate a random number within a specified range",
    {
        "type": "object",
        "properties": {
            "min": {
                "type": "integer",
                "description": "Minimum value (default: 0)",
                "default": 0
            },
            "max": {
                "type": "integer", 
                "description": "Maximum value (default: 100)",
                "default": 100
            }
        }
    },
    random_handler
)
```

## MCP Protocol

This server implements a basic subset of the MCP (Model Context Protocol):

- **Initialize**: Sets up the server and returns capabilities
- **Tools List**: Returns available tools and their schemas
- **Tools Call**: Executes a tool with provided arguments
- **Resources List**: Lists available resources (currently empty)

The server uses JSON-RPC 2.0 over stdio transport, making it compatible with MCP clients.

## Development

The server is designed to be simple and educational. Key components:

- `MCPServer` class: Core server implementation
- Tool handlers: Async functions that implement tool functionality
- JSON-RPC protocol handling: Request/response processing
- Stdio transport: Communication via stdin/stdout

This makes it perfect for demonstrating hot module reloading capabilities with `mcp-hmr`!