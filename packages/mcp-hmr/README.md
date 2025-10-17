# mcp-hmr

[![PyPI - Version](https://img.shields.io/pypi/v/mcp-hmr)](https://pypi.org/project/mcp-hmr/)
[![PyPI - Downloads](https://img.shields.io/pypi/dw/mcp-hmr)](https://pepy.tech/projects/mcp-hmr)

Provides [Hot Module Reloading](https://pyth-on-line.promplate.dev/hmr) for MCP/FastMCP servers.

It acts as **a drop-in replacement for `mcp run module:app` or `fastmcp run module:app`.** Both [FastMCP v2](https://github.com/jlowin/fastmcp) and the [official python SDK](https://github.com/modelcontextprotocol/python-sdk) are supported.

## Usage

If your server instance is named `app` in `path/to/main.py`, you can run:

```sh
mcp-hmr path.to.main:app
```

Which will be equivalent to the following code but with [HMR](https://github.com/promplate/hmr) enabled:

```py
from path.to.main import app

app.run("stdio")
```

Now, whenever you save changes to your source code, the server will automatically reload without dropping the connection to the client.
