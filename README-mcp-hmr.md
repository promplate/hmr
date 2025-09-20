# MCP-HMR: Hot Module Reloading for MCP Servers

A hot module reloading library for MCP (Model Context Protocol) servers, inspired by `uvicorn-hmr`. This library enables fast development cycles by reloading only the changed modules without restarting the entire server process.

## 🚀 Features

- **Hot Module Reloading**: Only reloads changed modules and their dependencies
- **Fast Development**: No need to restart the entire server process
- **MCP Protocol Support**: Designed specifically for MCP servers
- **File Watching**: Automatic detection of file changes
- **Dependency Tracking**: Smart reloading of dependent modules
- **Easy Integration**: Drop-in replacement for standard Python execution

## 📦 Installation

```bash
# Install from the workspace (for development)
cd packages/mcp-hmr
pip install -e .

# Or install dependencies manually
pip install dowhen hmr typer-slim watchfiles
```

## 🎯 Quick Start

### 1. Create an MCP Server

```python
# server.py
import asyncio
import json
import sys
from typing import Any, Dict, List

class MCPServer:
    def __init__(self, name: str = "my-server"):
        self.name = name
        self.tools = {}
    
    def add_tool(self, name: str, description: str, schema: Dict[str, Any], handler):
        self.tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": schema,
            "handler": handler
        }
    
    async def run_stdio(self):
        # MCP server implementation
        # ... (see examples/mcp-demo/server.py for full implementation)
        pass

server = MCPServer()

# Add your tools here
@server.add_tool("echo", "Echo messages", {...}, echo_handler)

async def main():
    await server.run_stdio()

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Run with Hot Reloading

Instead of:
```bash
python server.py
```

Use:
```bash
mcp-hmr server:main
```

### 3. Enjoy Fast Development

- Make changes to your server code
- Save the file
- See changes applied instantly without losing connection state!

## 📚 Examples

Check out the complete working example in [`examples/mcp-demo/`](examples/mcp-demo/):

```bash
cd examples/mcp-demo

# Run the demo server with hot reloading
mcp-hmr server:main --clear

# In another terminal, test the server
python3 client.py
```

The demo includes:
- Echo tool for message echoing
- Calculator tool for basic math
- Time tool for current time in various formats
- Interactive test client

## 🛠️ CLI Usage

```bash
mcp-hmr [OPTIONS] SLUG
```

**Arguments:**
- `SLUG`: Module and function to run (e.g., `server:main`, `mypackage.server:run_server`)

**Options:**
- `--reload-include`: Paths to include for watching (default: current directory)
- `--reload-exclude`: Paths to exclude from watching (default: `.venv`, `__pycache__`, `*.pyc`)
- `--transport`: Transport method - `stdio` or `sse` (default: `stdio`)
- `--clear`: Clear terminal before each reload
- `--help`: Show help message

## 🔄 How It Works

1. **File Monitoring**: Uses `watchfiles` to detect changes in your codebase
2. **Dependency Tracking**: Tracks module dependencies at runtime using `hmr`
3. **Smart Reloading**: Only reloads changed modules and their dependents
4. **Server Continuity**: Restarts the server function without killing the process
5. **State Preservation**: Maintains connection state during reloads

## 🆚 Comparison

| Traditional Development | With mcp-hmr |
|------------------------|-------------|
| Edit → Stop → Start → Test | Edit → Auto-reload → Test |
| Full process restart | Hot module reload only |
| Slower feedback loop | Instant feedback |
| May lose connection state | Preserves connection state |
| Higher resource usage | Lower resource usage |

## 🏗️ Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   File Watcher  │───▶│  HMR Engine     │───▶│  MCP Server     │
│   (watchfiles)  │    │  (hmr/reactiv.) │    │  (your code)    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Detect Changes  │    │ Reload Modules  │    │ Restart Server  │
│ (.py files)     │    │ (dependencies)  │    │ (same process)  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## 🔧 Development Setup

1. **Clone the repository**
2. **Install dependencies**:
   ```bash
   pip install -e packages/mcp-hmr
   ```
3. **Run the demo**:
   ```bash
   cd examples/mcp-demo
   mcp-hmr server:main --clear
   ```
4. **Test changes**:
   - Modify `server.py`
   - Watch automatic reloading in action!

## 🤝 Comparison with uvicorn-hmr

`mcp-hmr` is inspired by `uvicorn-hmr` but adapted for MCP servers:

| Feature | uvicorn-hmr | mcp-hmr |
|---------|-------------|---------|
| Target | ASGI/HTTP servers | MCP servers |
| Protocol | HTTP/WebSocket | JSON-RPC over stdio/sse |
| Transport | Network | stdio, sse |
| Use Case | Web applications | AI model context servers |

## 📄 License

MIT License - see the individual package files for details.

## 🙏 Acknowledgments

- Inspired by [`uvicorn-hmr`](https://github.com/promplate/hmr/tree/main/packages/uvicorn-hmr)
- Built on top of [`hmr`](https://github.com/promplate/hmr) for hot module reloading
- Uses [`watchfiles`](https://github.com/samuelcolvin/watchfiles) for file system monitoring

---

**Happy developing with hot reloading! 🔥**