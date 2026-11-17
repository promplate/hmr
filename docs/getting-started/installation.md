---
icon: lucide/package
---

# Installation

Use Python 3.12 or newer and install HMR into the same virtual environment as your application:

```sh
python -m pip install hmr
hmr --help
```

The `hmr` distribution provides both the `hmr` command and the `reactivity` Python package. Install your application's dependencies in that environment too.

For an isolated, dependency-free script you can also use:

```sh
uvx hmr path/to/entry.py
# or
pipx run hmr path/to/entry.py
```

These tools create isolated environments; they do not automatically reuse packages installed in your project's virtual environment. For a project with dependencies, use the project environment instead.

## Optional integrations

- ASGI: `python -m pip install uvicorn-hmr` ([guide](../integrations/uvicorn.md)). This has its own CLI options, not the entire Uvicorn CLI.
- MCP: `python -m pip install mcp-hmr` ([guide](../integrations/mcp.md)).
- ASGI with browser refresh: `python -m pip install 'uvicorn-hmr[all]'`. Quote extras to avoid shell glob expansion.
- Flask: install `flask` and use the [threaded example runner](../integrations/flask.md).

Integration packages install HMR as a dependency. They do not install every framework your app might use; for example, install `fastapi` separately for the FastAPI example.
