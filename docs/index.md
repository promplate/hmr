# Introduction

Hot Module Reload (HMR) for Python: fast, fine-grained reloads for your development workflow.

HMR allows parts of your app to be updated at runtime without a full rerun. Unlike traditional Python reloaders (such as `uvicorn --reload` or Flask's debug mode), HMR is much more efficient and robust—it only reruns what's necessary.

## Quick Start

Get started with HMR in seconds:

```sh
pip install hmr
hmr path/to/your/entry-file.py
```

Or with `uv`:

```sh
uvx hmr path/to/your/entry-file.py
```

See [Installation & Quick Start](./getting-started/intro.md) for details.

## Key Concepts

**HMR** means Hot Module Reload / [Hot Module Replacement](https://webpack.js.org/concepts/hot-module-replacement/). Three core principles:

- **On-demand**: Only rerun changed files and their affected dependents
- **Fine-grained**: Track variable-level dependencies, not just module-level
- **Push-pull reactivity**: Combine the efficiency of push notifications with pull-based dependency resolution

### How It Works

1. When you modify a file, HMR detects the change via file system monitoring
2. The affected module reruns, potentially changing its exported values
3. Modules depending on those values are automatically notified and rerun (recursively)
4. Other unaffected modules remain untouched—preserving app state and speeding up development

This is powered by a **reactive system** that tracks dependencies at runtime, unlike static analysis tools.

## Documentation Sections

### [Getting Started](./getting-started/intro.md)

Learn what HMR is and how to set it up:

- [What is HMR?](./getting-started/intro.md)
- [Installation](./getting-started/installation.md)
- [Quick Start Guide](./getting-started/quick-start.md)
- [Contributing](./contributing.md)

## Common Use Cases

### Web Framework Development

Use `uvicorn-hmr` for instant reloads in FastAPI/Starlette apps without losing state:

```sh
pip install uvicorn-hmr
uvicorn-hmr main:app
```

See [ASGI Integration](./integrations/uvicorn.md) for details.

### Script Development

Replace `python script.py` with `hmr script.py` for fast reloads:

```sh
hmr my_script.py arg1 arg2
```

See [Quick Start](./getting-started/quick-start.md) for details.

### MCP Servers

Use `mcp-hmr` for live updates in Model Context Protocol servers:

```sh
pip install mcp-hmr
mcp-hmr main:app
```

See [MCP Integration](./integrations/mcp.md) for details.

## Why HMR?

Imagine developing an ML service with a 5-second model initialization. With `uvicorn --reload`, every change—even a docstring update—restarts the app, forcing a 5-second wait. HMR eliminates this wait by only reloading what changed.

## Ecosystem

| Package              | Purpose                           |
| -------------------- | --------------------------------- |
| **hmr**              | Core reactive library + CLI       |
| **uvicorn-hmr**      | ASGI app reloading                |
| **mcp-hmr**          | MCP server reloading              |
| **hmr-daemon**       | Background module refreshing      |
| **fastapi-reloader** | Browser auto-refresh for web apps |

## Repository

This documentation complements the [official repository](https://github.com/promplate/pyth-on-line/tree/main/packages/hmr). For source code and issues, visit [promplate/hmr on GitHub](https://github.com/promplate/hmr).
