# MCP-HMR: Hot Module Reloading for Model Context Protocol Servers

[![PyPI - Version](https://img.shields.io/pypi/v/mcp-hmr)](https://pypi.org/project/mcp-hmr/)

MCP-HMR brings Hot Module Reload capabilities to Model Context Protocol (MCP) servers, similar to how `uvicorn-hmr` provides HMR for ASGI applications.

## Why MCP-HMR?

When developing MCP servers, traditional reload mechanisms restart the entire server process on any code change. This can be slow and disruptive, especially when:

- Your server loads large ML models that take time to initialize
- You have established database connections or connection pools
- You maintain complex caches or authentication contexts
- You're iterating quickly on tool or resource implementations

MCP-HMR offers a smarter approach: **incremental reloading** that updates only what changed while preserving long-lived state.

## Key Features

- 🔥 **Hot reloading**: Updates tools and resources without restarting the server
- 🧠 **Smart dependency tracking**: Only reloads what actually changed
- 📡 **Protocol-aware**: Sends capability change notifications to MCP clients
- 💾 **State preservation**: Maintains models, connections, and caches across reloads
- 🛠️ **Easy integration**: Simple decorators and CLI interface

## Installation

```bash
pip install mcp-hmr
```

## Quick Start

### 1. Decorate your MCP tools and resources

```python
from mcp_hmr import hmr_tool, hmr_resource, begin_module_declare, finalize_module_declare

# Begin module declaration (for cleanup tracking)
_gen = begin_module_declare()

@hmr_tool()
def calculate_sum(x: int, y: int) -> int:
    """Add two numbers together."""
    return x + y

@hmr_tool(schema={
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search query"}
    },
    "required": ["query"]
})
def search_documents(query: str) -> list[str]:
    """Search through document database."""
    # Your search implementation here
    return search_function(query)

@hmr_resource(uri_template="file://{path}")
def read_file(path: str) -> str:
    """Read contents of a file."""
    with open(path, 'r') as f:
        return f.read()

# Finalize module declaration (for cleanup)
finalize_module_declare(_gen)
```

### 2. Run your server with HMR

```bash
mcp-hmr my_mcp_server:create_server --reload-include src --reload-exclude .venv
```

### 3. Make changes and see them applied instantly!

Edit your tool functions, add new ones, or modify resource handlers. Changes take effect immediately without losing your server's state.

## CLI Usage

The `mcp-hmr` command provides a similar interface to `uvicorn-hmr`:

```bash
mcp-hmr [OPTIONS] MODULE:FUNCTION

Options:
  --reload-include TEXT     Paths to include in watching (default: current directory)
  --reload-exclude TEXT     Paths to exclude from watching (default: .venv)
  --log-level TEXT         Logging level (default: info)
  --clear                  Clear terminal before reloading
  --help                   Show help message
```

Examples:

```bash
# Basic usage
mcp-hmr server:create_server

# Watch specific directories
mcp-hmr server:create_server --reload-include src --reload-include lib

# Exclude additional paths  
mcp-hmr server:create_server --reload-exclude .venv --reload-exclude __pycache__

# Enable debug logging
mcp-hmr server:create_server --log-level debug
```

## How It Works

MCP-HMR leverages [hmr's fine-grained reactivity system](https://github.com/promplate/hmr) to provide efficient hot reloading:

1. **Reactive Registry**: Tools and resources are stored in reactive signals that automatically track dependencies
2. **Change Detection**: File watchers detect changes and trigger incremental updates
3. **Selective Reloading**: Only modules with actual changes are reloaded
4. **State Preservation**: Long-lived objects (models, connections) are preserved across reloads
5. **Protocol Integration**: MCP clients are notified of capability changes when supported

### Comparison with Traditional Approaches

| Feature | Traditional Reload | MCP-HMR |
|---------|-------------------|---------|
| **Server Restart** | Full process restart | Server keeps running |
| **State Loss** | All state lost | Preserves long-lived state |
| **Reload Speed** | Slow (full initialization) | Fast (incremental updates) |
| **Client Impact** | Connection lost | Seamless capability updates |
| **Memory Usage** | High (full reload) | Low (selective updates) |

## Advanced Usage

### State Preservation

Preserve expensive-to-recreate objects across reloads:

```python
from mcp_hmr.reloader import create_mcp_reloader

# In your server setup
reloader = create_mcp_reloader("server.py")

# Preserve loaded models
model = load_expensive_model()
reloader.preserve_state("main_model", model)

# Later, retrieve preserved state
model = reloader.get_preserved_state("main_model")
if model is None:
    model = load_expensive_model()
    reloader.preserve_state("main_model", model)
```

### Custom Tool Schemas

Provide detailed schemas for better MCP integration:

```python
@hmr_tool(schema={
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "The prompt to process"
        },
        "temperature": {
            "type": "number", 
            "description": "Sampling temperature",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 1.0
        }
    },
    "required": ["prompt"]
})
def generate_text(prompt: str, temperature: float = 1.0) -> str:
    """Generate text using the language model."""
    # Your implementation here
    pass
```

### Resource Templates

Define flexible resource URIs:

```python
@hmr_resource(uri_template="db://documents/{id}")
def get_document(id: str) -> dict:
    """Retrieve a document by ID."""
    return database.get_document(id)

@hmr_resource(uri_template="cache://{key}")  
def get_cached_value(key: str) -> str:
    """Get value from cache."""
    return cache.get(key)
```

## Integration Examples

### With FastMCP

```python
from fastmcp import FastMCP
from mcp_hmr import hmr_tool, begin_module_declare, finalize_module_declare

app = FastMCP("My Server")
_gen = begin_module_declare()

@app.tool()
@hmr_tool()
def my_tool(param: str) -> str:
    """My hot-reloadable tool."""
    return f"Processed: {param}"

finalize_module_declare(_gen)

def create_server():
    return app
```

### With Standard MCP Libraries

```python
from mcp import Server
from mcp_hmr import hmr_tool, begin_module_declare, finalize_module_declare

server = Server("my-server")
_gen = begin_module_declare()

@hmr_tool()
def process_data(data: str) -> dict:
    """Process incoming data."""
    # Your processing logic
    return {"result": data.upper()}

# Register with MCP server
server.add_tool("process_data", process_data)

finalize_module_declare(_gen)

def create_server():
    return server
```

## Configuration

### Environment Variables

- `MCP_HMR_LOG_LEVEL`: Set logging level (default: "info")
- `MCP_HMR_CLEAR_TERMINAL`: Clear terminal before reloads (default: "false")

### Programmatic Configuration

```python
from mcp_hmr.reloader import create_mcp_reloader
from mcp_hmr.notifications import set_mcp_capability_support

# Configure capability notifications
set_mcp_capability_support(True)  # Enable if client supports dynamic capabilities

# Create custom reloader
reloader = create_mcp_reloader(
    "server.py",
    reload_include=["src", "lib"],
    reload_exclude=[".venv", "tests"],
    on_change_callback=lambda: print("Server reloaded!")
)
```

## Troubleshooting

### Common Issues

1. **"Module already imported" error**
   - Make sure to call `reactivity.hmr.core.patch_meta_path()` before importing your server module

2. **Tools not updating after changes**
   - Ensure you're using the `@hmr_tool` decorator
   - Check that `finalize_module_declare()` is called at module level

3. **Client not receiving capability notifications**
   - Verify your MCP client supports dynamic capability updates
   - Check if `set_mcp_capability_support(True)` is called

4. **State not preserved across reloads**
   - Use the reloader's `preserve_state()` method for objects you want to keep
   - Ensure preserved objects are pickleable or use weak references

### Debug Mode

Enable debug logging to see detailed reload information:

```bash
mcp-hmr server:create_server --log-level debug
```

## Limitations

- **MCP Protocol Version**: Requires MCP clients that support capability change notifications (optional)
- **Python Version**: Requires Python 3.12+ (for `hmr` compatibility)
- **Development Only**: Intended for development environments, not production

## Contributing

Contributions are welcome! Please see the [main HMR repository](https://github.com/promplate/hmr) for contribution guidelines.

## License

MIT License - see the [main repository](https://github.com/promplate/hmr) for details.