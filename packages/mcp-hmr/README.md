# mcp-hmr

[![PyPI - Version](https://img.shields.io/pypi/v/mcp-hmr)](https://pypi.org/project/mcp-hmr/)
[![PyPI - Downloads](https://img.shields.io/pypi/dw/mcp-hmr)](https://pepy.tech/projects/mcp-hmr)

Provides [Hot Module Reloading](https://pyth-on-line.promplate.dev/hmr) for MCP/FastMCP servers.

It acts as **a drop-in replacement for `mcp run path:app` or `fastmcp run path:app`.** Both [FastMCP v2](https://github.com/jlowin/fastmcp) and the [official python SDK](https://github.com/modelcontextprotocol/python-sdk) are supported. Compatible libraries like [mcp-use](https://github.com/mcp-use/mcp-use/tree/main/libraries/python) are also supported.

> [!TIP]
>
> Here's the thing: With this package, you don't need to restart your MCP server on every code change. Just save your files, and the server automatically uses the latest code without dropping client connections.

## CLI Usage

If your server instance is named `app` in `./path/to/main.py`, you can run:

```sh
mcp-hmr ./path/to/main.py:app
```

Which will be equivalent to `fastmcp run ./path/to/main.py:app` but with [HMR](https://github.com/promplate/hmr) enabled.

Or using module import format:

```sh
mcp-hmr main:app
```

Now, whenever you save changes to your source code, the server will automatically reload without dropping the connection to the client.

The CLI provides a simple way to get started, but if you need more control, you can also use `mcp_hmr` programmatically.

### Options

The options are aligned with those of [`fastmcp run`](https://gofastmcp.com/patterns/cli#fastmcp-run), providing a familiar interface if you're already using FastMCP.

```sh
mcp-hmr --help
```

The command supports the following options:

| Option               | Description                                                   | Default                       |
| -------------------- | ------------------------------------------------------------- | ----------------------------- |
| `--transport` / `-t` | Transport protocol: `stdio`, `sse`, `http`, `streamable-http` | `stdio`                       |
| `--log-level` / `-l` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`    | -                             |
| `--host`             | Host to bind to for HTTP/SSE transports                       | `localhost`                   |
| `--port`             | Port to bind to for HTTP/SSE transports                       | `8000`                        |
| `--path`             | Route path for the server                                     | `/mcp` (http) or `/sse` (sse) |
| `--stateless`        | Sets `stateless_http=True` and `json_response=True`           | `False`                       |
| `--no-cors`          | Disable CORS for HTTP transport                               | `False`                       |

> [!NOTE]
> HTTP transport uses a permissive CORS policy by default for development convenience.

### Examples

Start with HTTP transport on custom host, port and path:

```sh
mcp-hmr main:app -t http --host 0.0.0.0 --port 56789 --path /
```

Start with debug logging:

```sh
mcp-hmr main:app -l DEBUG
```

Use streamable-http transport:

```sh
mcp-hmr main:app -t streamable-http --port 8000
```

## Programmatic API

For more advanced use cases, you can integrate `mcp_hmr` directly into your existing codebase.
Say you have a FastAPI server `app`:

```python
from mcp_hmr import mcp_server
from uvicorn import Config, Server

app: FastAPI = ...

async with mcp_server("path/to/mcp-server.py:mcp") as mcp:
    # mcp.add_middleware(...)
    app.mount("/", mcp.http_app("/mcp"))  # mount the auto-reloading MCP server to your FastAPI app
    await Server(Config(app)).serve()
```

This is useful when you want to extend your server with additional features like CORS middleware while maintaining HMR capabilities.

> [!NOTE]
>
> ### Tips for using the programmatic API
>
> 1. Provide the correct target import path `mcp_server("path/to/mcp-server.py:mcp")` / `mcp_server("module.submodule:mcp")`. Test it first with `mcp-hmr <target>`.
> 2. The target module must **not be imported yet** when calling `mcp_server(target)`, or hot-reloading won't work.
> 3. Hot reloading only works within the `async with mcp_server(...) as mcp:` block. Especially caution for cases that code implicitly exit this block like:
>
> ```python
> async with anyio.create_task_group() as tg mcp_server("path/to/mcp-server.py:mcp") as mcp:
>     tg.start_soon(mcp.run_async)
> ```
>
> Here, `mcp.run_async()` is awaited when the `async with anyio.create_task_group()` block exits, but the `mcp_server` context is already closed. Fix this by swapping the context manager order:
>
> ```python
> async with mcp_server("...") as mcp, create_task_group() as tg:  # note the order
>     tg.start_soon(mcp.run_async)
> ```
>
> Or await the `mcp.run_async()` task within the same block:
>
> ```python
> async with anyio.create_task_group() as tg, mcp_server("...") as mcp:
>     event = anyio.Event()
>
>     @tg.start_soon
>     async def _():
>         await mcp.run_async()
>         event.set()
>
>     await event.wait()
> ```
>
> Of course the simplest approach is `await mcp.run_async()` directly. The task group example above assumes you already have some existing lifespan management that requires running the MCP server within a task group.
