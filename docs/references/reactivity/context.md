---
icon: lucide/circle-dashed
title: Reactivity Context
---

# `#!py from reactivity.context import *`

Reactivity Contexts define the execution context that reactive computations run inside.

For simple programs you usually rely on the default context implicitly.
This module becomes more interesting when you want:

- isolated reactive subsystems that should not subscribe to one another
- shorthand constructors like `ctx.signal(...)`, `ctx.effect(...)`, `ctx.derived(...)`
- async dependency tracking that survives across coroutine scheduling boundaries

In particular, async primitives use the context's async execution state to keep tracking coherent after `await`.
That is why async reactivity in HMR is more than "just check the call stack".

For a higher-level introduction, see [Advanced Reactivity](../../reactive/advanced.md) and [Async Reactive Primitives](./async_primitives.md).

::: reactivity.context
