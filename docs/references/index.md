# API Reference

Complete reference for HMR's public API.

## Overview

The HMR library provides two main layers:

1. **Reactive primitives** (`reactivity` package): Signals, effects, derived values for building reactive applications
2. **HMR runtime** (`reactivity.hmr` package): Module reloading infrastructure and hooks

For detailed API signatures and examples, see [api.md](./api.md).

## Quick Links

**Reactive Basics:**

- [Signals](../reactive/signals.md) - Observable values
- [Effects](../reactive/effects.md) - Side effects that react to changes
- [Derived Values](../reactive/derived.md) - Computed values
- [Advanced Patterns](../reactive/advanced.md) - Batching, contexts, async

**Framework Integration:**

- [Uvicorn (ASGI)](../integrations/uvicorn.md)
- [Flask](../integrations/flask.md)
- [MCP](../integrations/mcp.md)

**Source Code:**

The definitive reference is the source code:

- Reactive primitives: `reactivity/primitives.py`, `reactivity/_curried.py`, `reactivity/helpers.py`
- Async support: `reactivity/async_primitives.py`
- Reactive containers: `reactivity/collections.py`
- HMR runtime: `reactivity/hmr/core.py`, `reactivity/hmr/run.py`

## Stability

- Requires Python 3.12+
- Async primitives support asyncio and trio via pluggable task factories
- HMR aims at non-breaking development reloads; prefer full restarts for C-level or protocol-breaking changes
