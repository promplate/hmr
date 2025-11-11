# What is HMR?

Hot Module Reload (HMR) allows you to update your Python application at runtime without a full process restart. Unlike traditional cold-reloading (like `watchfiles` CLI or `uvicorn --reload`), HMR only re-executes the code that actually changed.

## How It Works

When a file changes:

1. The module whose source file you changed will rerun
2. Any modules that depend on it will also rerun
3. Other unaffected modules stay untouched

This preserves your application state, enabling a smoother development experience.

## Core Differences

**HMR vs. Cold Reloading:**

- Cold reload: Restart the entire process (lost state, slower)
- HMR: Rerun only affected modules (state preserved, faster)

**HMR vs. Static Analysis:** HMR uses fine-grained, runtime dependency tracking instead of AST analysis. This means:

- It tracks actual variable-level dependencies during execution
- It works with dynamic imports and runtime metaprogramming
- No complex configuration needed

## Getting Started

Choose your workflow:

- **CLI Tool**: Use `hmr` as a drop-in replacement for `python`
- **Framework Integration**: Use `uvicorn-hmr` for ASGI apps or `fastapi-reloader` for FastAPI
- **Library**: Use the reactive primitives in your own code

See [Installation](./installation.md) and [Quick Start](./quick-start.md) for next steps.
