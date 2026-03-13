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
from anyio import sleep
from reactivity import signal, async_derived, async_effect

count = signal(1)

async def main():
    @async_derived
    async def doubled():
        await sleep(0.01)
        return count.get() * 2

    @async_effect
    async def printer():
        print(await doubled())

    await sleep(0.03)
    count.set(2)
    await sleep(0.03)
```

In IPython or a notebook, run `await main()`.
In a script, the release notes pattern is `from anyio import run; run(main)`.

Creating `printer` schedules an initial run by default. Later, `count.set(2)` marks `doubled` dirty and schedules the effect again, so this example prints `2` and then `4`.

Because the work is asynchronous, you usually observe the rerun only after control returns to the event loop.

## Custom Task Ownership

```python
from asyncio import TaskGroup, sleep
from reactivity import async_effect


async def main():
    async with TaskGroup() as tg:

        @async_effect(
            task_factory=lambda fn: tg.create_task(fn()),
            call_immediately=False,
        )
        async def printer():
            await sleep(0.01)
            print("tick")

        await printer()  # manually trigger the first run
        await sleep(0.02)
```

Use this pattern when tasks must belong to a specific supervisor instead of the library's default scheduler.

## Important Semantics

- `AsyncEffect(..., call_immediately=False)` defers the first run until you explicitly await or otherwise trigger it.
- `AsyncDerived(..., check_equality=True)` keeps the same equality-based notification behavior as sync `derived`; equal recomputations do not notify subscribers.
- Tracking happens when you call an `AsyncDerived`, not when the returned awaitable is later awaited. In normal code, `await total()` is fine because the call and await happen together.
- Concurrent callers share the same in-flight recomputation. If a recompute is already running, later callers await the same task instead of starting duplicate work.
- Before recomputing an `AsyncDerived`, the implementation synchronizes dirty derived dependencies first, including mixed sync/async derived chains.
- `dispose()` removes subscriptions so future dependency changes stop auto-triggering the computation. It does not cancel tasks that were already scheduled.

## API Reference

<!-- deno-fmt-ignore-start -->
::: reactivity.async_primitives
    options:
        show_inheritance_diagram: true
<!-- deno-fmt-ignore-end -->
