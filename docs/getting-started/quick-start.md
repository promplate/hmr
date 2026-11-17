---
icon: lucide/rocket
---

# Quick start

After [installing HMR](installation.md), create these two files in one directory.

```python title="greet.py"
def message():
    return "Hello, World!"
```

```python title="main.py"
from greet import message

print(message())
```

Run from that directory:

```sh
hmr main.py
```

The first message prints immediately. The process then waits for changes. Change the return value in `greet.py` to `"Hello, HMR!"` and save: `main.py` runs again and prints the new message, without starting a new Python process. Stop with Ctrl+C.

The import reads `greet.message`, so HMR records a dependency. Changing that function invalidates the dependent entry module. This does not mean arbitrary existing references or every object in memory are automatically updated.

## Running a module

```sh
hmr -m mypackage
```

As with `python -m mypackage`, a package needs a `__main__.py`. Arguments following the entry path or module name are passed to the program.

## Servers need an integration

The plain `hmr` command executes the entry file before it starts watching. A blocking `app.run()` or infinite loop at module scope prevents that watcher from starting.

- [ASGI / FastAPI](../integrations/uvicorn.md): `uvicorn-hmr main:app`
- [MCP](../integrations/mcp.md): `mcp-hmr main:app`
- [Flask](../integrations/flask.md): use the example's threaded `start.py`, then `hmr start.py`

Start with the [plain-module demo](../integrations/demo.md) to inspect dependency propagation without a server. See [reactive programming](../reactive/index.md) for explicit reactive state.
