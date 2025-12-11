# mcp-hmr

[![PyPI - Version](https://img.shields.io/pypi/v/mcp-hmr)](https://pypi.org/project/mcp-hmr/)
[![PyPI - Downloads](https://img.shields.io/pypi/dw/mcp-hmr)](https://pepy.tech/projects/mcp-hmr)

Provides [Hot Module Reloading](https://pyth-on-line.promplate.dev/hmr) for MCP/FastMCP servers.

It acts as **a drop-in replacement for `mcp run path:app` or `fastmcp run path:app`.** Both [FastMCP v2](https://github.com/jlowin/fastmcp) and the [official python SDK](https://github.com/modelcontextprotocol/python-sdk) are supported.

## Usage

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

## Options

The options are aligned with those of [`fastmcp run`](https://gofastmcp.com/patterns/cli#fastmcp-run), providing a familiar interface if you're already using FastMCP.

```sh
mcp-hmr --help
```

The command supports the following options:

| Option               | Description                                                   | Default                           |
| -------------------- | ------------------------------------------------------------- | --------------------------------- |
| `--transport` / `-t` | Transport protocol: `stdio`, `sse`, `http`, `streamable-http` | `stdio`                           |
| `--log-level` / `-l` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`    | -                                 |
| `--host`             | Host to bind to for HTTP/SSE transports                       | `localhost`                       |
| `--port`             | Port to bind to for HTTP/SSE transports                       | `8000`                            |
| `--path`             | Route path for the server                                     | `/mcp` (http) or `/mcp/sse` (sse) |

### Examples

Start with HTTP transport on custom host, port and path:

```sh
mcp-hmr main:app -t http --host 0.0.0.0 --port 56789 --path /
```

Start with debug logging:

```sh
mcp-hmr main:app -l DEBUG
```
