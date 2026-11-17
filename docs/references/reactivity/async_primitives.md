---
icon: lucide/circle-dashed
title: Async Reactive Primitives
---

# `#!py from reactivity.async_primitives import *`

The module `reactivity.async_primitives` contains the async counterparts of HMR's core reactive primitives.
The dependency model is the same as the sync API, but actual work is scheduled onto an async runtime instead of running to completion inline.

## What Lives Here

- `AsyncEffect` / `async_effect` for async side effects
- `AsyncDerived` / `async_derived` for cached async computations
- `TaskFactory` / `default_task_factory` for integrating with an event loop or task supervisor

## Mental Model

- `async_effect` behaves like `effect`, except each trigger schedules an awaitable task instead of finishing immediately on the current stack.
- `async_derived` behaves like `derived`, except you read it with `await value()`.
- Dirty async derived values are still lazy. If nothing is pulling them, they stay dirty until the next read.
- If an effect or some other non-derived subscriber is pulling an async derived value, invalidation can eagerly schedule a rerun.
- Internally, the current reactive context is forked before the coroutine runs, so dependency tracking still works across the async boundary.

## Runtime Support

`default_task_factory` is intentionally small in scope. In the current implementation it:

- uses `asyncio.ensure_future(...)` on `asyncio`
- spawns a Trio system task on `trio`
- falls back to `asyncio.ensure_future(...)` on `emscripten`
- raises `AsyncLibraryNotFoundError` for other async libraries

With the default task factory, create async primitives inside an active `asyncio` / `trio` runtime.
If your application already owns task lifetime through `asyncio.TaskGroup` or a Trio nursery, pass a custom `task_factory`.

A `TaskFactory` receives a zero-argument async function and must schedule it, returning an awaitable for the eventual result.

## Basic Example

```python
import asyncio
from reactivity import signal, async_derived, async_effect


async def main():
    count = signal(1)
    updated = asyncio.Event()

    @async_derived
    async def doubled():
        await asyncio.sleep(0)
        return count.get() * 2  # reads after await are tracked too

    @async_effect(call_immediately=False)
    async def printer():
        print(await doubled())
        updated.set()

    try:
        await printer()  # prints 2 and establishes subscriptions
        updated.clear()
        count.set(2)
        await asyncio.wait_for(updated.wait(), timeout=1)  # prints 4
    finally:
        printer.dispose()
        doubled.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

In a notebook with an active event loop, use `await main()` instead of `asyncio.run(main())`.

The first explicit call establishes subscriptions before `count` changes. The event waits for the subsequent effect run, rather than assuming a fixed sleep is long enough. With the default `call_immediately=True`, construction schedules the initial run instead; give that run time to establish subscriptions before relying on updates.

## Custom Task Ownership

```python
from asyncio import TaskGroup, sleep
from reactivity import async_effect, signal


async def main():
    tick = signal("tick")
    async with TaskGroup() as tg:

        @async_effect(
            task_factory=lambda fn: tg.create_task(fn()),
            call_immediately=False,
        )
        async def printer():
            await sleep(0.01)
            print(tick.get())

        await printer()  # manually trigger the first run
        printer.dispose()
```

Use this pattern when tasks must belong to a specific supervisor instead of the library's default scheduler.

## Important Semantics

- `AsyncEffect(..., call_immediately=False)` defers the first run until you explicitly await or otherwise trigger it.
- `AsyncDerived(..., check_equality=True)` keeps the same equality-based notification behavior as sync `derived`; equal recomputations do not notify subscribers.
- Tracking happens when you call an `AsyncDerived`, not when the returned awaitable is later awaited. In normal code, `await total()` is fine because the call and await happen together.
- Concurrent reads share an in-flight recomputation while it remains valid. Invalidation clears that task reference, so a new read after invalidation can start another run while the old one is still in flight. Do not assume cancellation, serialization, or latest-result-wins behavior.
- Before recomputing an `AsyncDerived`, the implementation synchronizes dirty derived dependencies first, including mixed sync/async derived chains.
- `dispose()` removes current subscriptions. It does not cancel tasks that were already scheduled. A running task can still perform side effects and collect dependencies after disposal; coordinate shutdown with the task owner rather than treating disposal as cancellation.
- Async effects schedule a task for each trigger; rapid changes can overlap runs. Use explicit task ownership and application-level coordination when overlap would be unsafe.

## Verification scope

The asyncio examples and core semantics above were exercised against hmr 0.7.6.3 and [core commit cdf0c657](https://github.com/promplate/pyth-on-line/tree/cdf0c6579ab58b90306eee99bf477b6d980aab05/packages/hmr/reactivity). Trio and Emscripten runtime behavior was inspected in source, not executed. The tests cover ordinary propagation and concurrent reads, not every overlapping invalidation race.

## API Reference

<!-- deno-fmt-ignore-start -->
::: reactivity.async_primitives
    options:
        show_inheritance_diagram: true
<!-- deno-fmt-ignore-end -->
