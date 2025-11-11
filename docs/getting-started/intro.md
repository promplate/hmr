# Introduction

HMR (Hot Module Reload / Hot Module Replacement) updates running Python code when source files change, without restarting the whole process. Only the changed module and the code that depends on it are re-executed, preserving in-memory state and speeding up the edit–test loop.

## Why HMR?

**Traditional reload** (like `uvicorn --reload` or `watchfiles`) restarts the entire process on every change. This means:

- Heavy objects (DB clients, ML models) need to be reinitialized every time
- All modules are reimported, even unchanged ones
- Startup time compounds with project size

**HMR reload** is smarter:

- Only changed modules and their dependents are re-executed
- Unaffected code and state stay untouched
- Runtime dependency tracking ensures accurate invalidation

This makes development much faster, especially for large projects with expensive initialization.

## How It Works

1. **File watching**: Detects when a source file changes
2. **Dependency tracking**: Uses runtime reactive tracking to know what depends on what
3. **Selective reload**: Re-executes only the changed module and its dependents
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
mcp-hmr server:app
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

| Package            | Purpose                          |
| ------------------ | -------------------------------- |
| `hmr`              | Core library + CLI               |
| `uvicorn-hmr`      | ASGI integration (FastAPI, etc.) |
| `mcp-hmr`          | MCP server integration           |
| `fastapi-reloader` | Browser auto-refresh for FastAPI |
| `hmr-daemon`       | Background module refresh daemon |

## Next Steps

- [Installation](./installation.md): Install HMR
- [Quick Start](./quick-start.md): Try a simple example
