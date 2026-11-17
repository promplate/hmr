---
icon: simple/modelcontextprotocol
---

# MCP — mcp-hmr

`mcp-hmr` runs a proxy MCP server and reloads the underlying FastMCP application when its reactive dependencies change. It accepts the `fastmcp.FastMCP` class and the SDK's `mcp.server.FastMCP` class; it is not a generic replacement for every MCP server launcher.

## Install and run

```sh
python -m pip install mcp-hmr
```

```python title="main.py"
from fastmcp import FastMCP

app = FastMCP("example")

@app.tool
def echo(message: str) -> str:
    return f"Echo: {message}"
```

Run from your project directory so the watcher covers its source files:

```sh
mcp-hmr main:app
# A file path also works:
mcp-hmr ./main.py:app
```

The default transport is stdio. Let an MCP client launch this command; it is not an interactive terminal prompt. Do not call `app.run()` at import time, since the integration runs the transport itself.

With a client connected, call `echo`, edit its return value, save, and call it again. In the stdio example, subsequent tool calls through the same client connection use the updated implementation. This does not guarantee that in-flight tool calls, arbitrary application state, or client-side cached tool schemas survive a change.

## HTTP and options

```sh
mcp-hmr main:app --transport http --host 127.0.0.1 --port 8000
mcp-hmr --help
```

The CLI offers `stdio`, `http`, `sse` and `streamable-http` transports, plus `--log-level` and `--path`. `--stateless` and `--no-cors` are accepted only with the literal `--transport http` option. HTTP mode enables permissive CORS by default; use `--no-cors` if that is not desired. Keep development servers bound to a trusted local interface.

The published package currently constrains FastMCP to `>=2.2.0,<3`. Its proxy integration uses FastMCP internals, so check compatibility before changing that dependency range.

See the [repository example and client](https://github.com/promplate/hmr/tree/docs/examples/mcp) and [API reference](../references/mcp-hmr.md). The stdio reload flow is the minimal tested path; other transports and client reconnection behavior should be tested for your application.
