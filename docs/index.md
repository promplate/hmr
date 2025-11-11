# HMR for Python

HMR (Hot Module Reload) lets you update running Python code without a full restart. It's a powerful tool for speeding up your development workflow, whether you're building web apps, data services, or just running scripts.

- **Faster development**: See changes instantly without losing application state.
- **Fine-grained reloading**: Only re-executes code that has actually changed.
- **Wide integration**: Works with popular frameworks like FastAPI, Flask, and MCP.

## Getting Started

- **[Introduction](./getting-started/intro.md)**: What is HMR and why use it?
- **[Installation](./getting-started/installation.md)**: How to install the tools.
- **[Quick Start](./getting-started/quick-start.md)**: A simple "hello world" example.

## Reactive Programming

HMR is built on a fine-grained reactive library. You can use these primitives to build your own reactive applications.

- **[Signals](./reactive/signals.md)**: The core reactive primitive.
- **[Effects](./reactive/effects.md)**: Create side effects that react to changes.
- **[Derived Values](./reactive/derived.md)**: Create new values that depend on other reactive values.
- **[Advanced](./reactive/advanced.md)**: Advanced reactive patterns.

## Integrations

Drop-in tools that bring HMR to your favorite frameworks.

- **[Uvicorn](./integrations/uvicorn.md)**: For ASGI applications (FastAPI, Starlette).
- **[Flask](./integrations/flask.md)**: For Flask applications.
- **[MCP](./integrations/mcp.md)**: For MCP/FastMCP servers.
- **[Demo](./integrations/demo.md)**: A general-purpose file watcher.

## References

- **[API Reference](./references/api.md)**: Complete API documentation.
- **[Contributing](./contributing.md)**: How to contribute to the project.
