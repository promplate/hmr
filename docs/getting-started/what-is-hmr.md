---
icon: lucide/star
---

# What is HMR

HMR (Hot Module Reload / Hot Module Replacement) updates running Python code when source files change, without restarting the whole process. Changed modules are re-executed; dependency tracking propagates updates through the names read by other reactive computations. This helps preserve state in unaffected modules and speeds up your edit-test loop.

## Why HMR?

**Traditional reload** (like `uvicorn --reload` or `watchfiles`) restarts the entire process on every change. This means:

- Heavy objects (DB clients, ML models) need to be reinitialized every time
- All modules are reimported, even unchanged ones
- Startup time compounds with project size

**HMR reload** is smarter:

- Changed modules are re-executed, and tracked name dependencies determine which consumers need updating
- Unaffected modules can retain their state; state created in re-executed code may be recreated
- Runtime dependency tracking provides fine-grained invalidation

This makes development much faster, especially for large projects with expensive initialization.

## How It Works

1. **File watching**: Detects when a source file changes
2. **Dependency tracking**: Uses runtime reactive tracking to know what depends on what
3. **Selective reload**: Re-executes changed modules and updates affected consumers through tracked names
4. **State preservation**: Unaffected modules and objects remain in memory

Unlike static analysis tools, HMR tracks dependencies at runtime using a reactive system. This gives fine-grained, name-level precision.

## When to Use HMR

**Good fits:**

- Web development (FastAPI, Flask, Starlette)
- MCP server development
- Long-running scripts with expensive setup
- Iterative data analysis with heavy models

**When to restart instead:**

- Native extensions or C-level state changes
- Protocol-breaking changes in APIs
- When you need guaranteed clean state

## Three Ways to Use HMR

**1. As a CLI** (drop-in replacement for `python`)

```sh
hmr path/to/script.py
hmr -m your.module
```

**2. Framework integrations** (dedicated packages)

```sh
pip install uvicorn-hmr
uvicorn-hmr main:app

pip install mcp-hmr
mcp-hmr main:app
```

**3. As a library** (reactive primitives)

```python
from reactivity import signal, derived, effect

count = signal(0)

@derived
def doubled():
    return count.get() * 2

@effect
def logger():
    print(f"Doubled: {doubled()}")
```

## Ecosystem

| Package                                                                                                      | Use Case                                                          |
| ------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| [`hmr`](https://pypi.org/project/hmr/ "hmr core package")                                                    | Reactive programming lib and hot reloading                        |
| [`uvicorn-hmr`](https://pypi.org/project/uvicorn-hmr/ "HMR-enabled Uvicorn server for ASGI apps")            | ASGI integration (FastAPI, etc.)                                  |
| [`mcp-hmr`](https://pypi.org/project/mcp-hmr/ "HMR-enabled MCP/FastMCP server")                              | MCP server integration                                            |
| [`fastapi-reloader`](https://pypi.org/project/fastapi-reloader/ "An ASGI middleware for browser refreshing") | Browser auto-refresh for ASGI apps, often used with `uvicorn-hmr` |
| [`hmr-daemon`](https://pypi.org/project/hmr-daemon/ "Background daemon that refreshes modules on changes")   | Keep variables up-to-date in a daemon thread                      |

## Next Steps

- [Installation](./installation.md): Install HMR
- [Quick Start](./quick-start.md): Try a simple example
