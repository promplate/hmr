# mcp-hmr

[![PyPI - Version](https://img.shields.io/pypi/v/mcp-hmr)](https://pypi.org/project/mcp-hmr/)

This package provides hot module reloading (HMR) for MCP (Model Context Protocol) servers.

It uses [`watchfiles`](https://github.com/samuelcolvin/watchfiles) to detect file system modifications,
re-executes the corresponding modules with [`hmr`](https://github.com/promplate/pyth-on-line/tree/main/packages/hmr) and restarts the MCP server (in the same process).

**HOT** means the main process never restarts, and reloads are fine-grained (only the changed modules and their dependent modules are reloaded).
Since the Python module reloading is on-demand and the server is not restarted on every save, it provides a much faster development experience.

## Why?

1. When developing MCP servers, you typically need to restart the entire process on every code change, but restarting the whole process is unnecessary:
   - There is no need to restart the Python interpreter, neither all the 3rd-party packages you imported.
   - Your changes usually affect only one single file, the rest of your application remains unchanged.
2. `mcp-hmr` tracks dependencies at runtime, remembers the relationships between your modules and only reruns necessary modules.
3. So you can save a lot of time by not restarting the whole process on every file change. You can see a significant speedup for debugging large MCP applications.
4. Although magic is involved, we thought and tested them very carefully, so everything works as expected:
   - Your lazy loading through module-level `__getattr__` still works
   - Your runtime imports through `importlib.import_module` or even `__import__` still work
   - Even valid circular imports between `__init__.py` and sibling modules still work
   - Fine-grained dependency tracking in the above cases still work
   - Decorators still work, even meta programming hacks like `getsource` calls work too
   - Standard dunder metadata like `__name__`, `__doc__`, `__file__`, `__package__` are correctly set

## Installation

```sh
pip install mcp-hmr
```

## Usage

Instead of running your MCP server directly:

```sh
python server.py
```

Use `mcp-hmr` to run it with hot module reloading:

```sh
mcp-hmr server:main
```

Where `server:main` means calling the `main()` function in `server.py`.

Everything will work as expected, but with **hot** module reloading.

## CLI Arguments

- `slug`: The module and function to run (e.g., `server:main`, `mypackage.server:run_server`)
- `--reload-include`: Paths to include for watching (defaults to current directory)
- `--reload-exclude`: Paths to exclude from watching (defaults to `.venv`, `__pycache__`, `*.pyc`)
- `--transport`: Transport method - `stdio` or `sse` (defaults to `stdio`)
- `--clear`: Clear the terminal before each reload

## Example

Create a simple MCP server:

```python
# server.py
import asyncio
from mcp import Server
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types

server = Server("example-server")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="echo",
            description="Echo back the input",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to echo"}
                },
                "required": ["message"]
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "echo":
        message = arguments.get("message", "")
        return [types.TextContent(type="text", text=f"Echo: {message}")]
    else:
        raise ValueError(f"Unknown tool: {name}")

async def main():
    # Run the server using stdin/stdout streams
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="example-server",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
```

Then run it with hot reloading:

```sh
mcp-hmr server:main
```

Now when you modify `server.py`, the server will automatically reload without losing its connection state!

## Transport Options

MCP servers can use different transport mechanisms:

- `--transport stdio`: Use stdin/stdout (default, works with most MCP clients)
- `--transport sse`: Use Server-Sent Events (useful for web-based clients)

## Development

The behavior of `reload_include` and `reload_exclude` is as follows:

1. Only file or directory paths are allowed; patterns will be treated as literal paths.
2. Currently only supports hot-reloading Python source files.
3. If you do not provide `reload_include`, the current directory is included by default; if you do provide it, only the specified paths are included. The same applies to `reload_exclude`.

The following options are supported:

- `--clear`: Wipes the terminal before each reload. Just like `vite` does by default.

## Comparison with Regular MCP Development

| Regular MCP Development | With mcp-hmr |
|------------------------|-------------|
| Edit code → Stop server → Start server → Test | Edit code → Automatic reload → Test |
| Full process restart | Hot module reload only |
| Slower feedback loop | Instant feedback |
| May lose connection state | Preserves connection state |