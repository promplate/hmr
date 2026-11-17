---
icon: lucide/loader-circle
---

# Advanced Reactivity

!!! note "Verification scope"

    The examples and semantics on this page were checked against hmr 0.7.6.3 and the [current core source](https://github.com/promplate/pyth-on-line/tree/cdf0c6579ab58b90306eee99bf477b6d980aab05/packages/hmr/reactivity). This is a source and runtime check, not a claim of human review. Async runtime checks cover asyncio; Trio and browser runtimes were not exercised.

HMR uses **push-pull reactivity**: signals push notifications when updated, but derived values are lazy and only recompute when read (pulled) or when a hard puller (like an effect) forces evaluation. This creates a dependency graph where data flows from sources to consumers.

## Core primitives

```python
from reactivity import signal, state, effect, derived, memoized, batch, new_context, reactive
```

- [`signal`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/primitives.py#L91 "hmr reactivity: Signal class — GitHub") / [`state`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/primitives.py#L181 "hmr reactivity: State class — GitHub"): observables
- [`effect`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/primitives.py#L204 "hmr reactivity: Effect class — GitHub"): side effects
- [`derived`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/primitives.py#L262 "hmr reactivity: Derived class — GitHub"): lazy computed
- [`memoized`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/helpers.py#L10 "hmr reactivity: Memoized class — GitHub"): cache invalidation without result equality filtering
- [`batch`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/primitives.py#L218 "hmr reactivity: Batch class — GitHub"): group updates
- [`new_context`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/context.py#L94 "hmr reactivity: new_context function — GitHub"): isolated environment
- [`reactive`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/collections.py#L430 "hmr reactivity: reactive function — GitHub"): reactive containers

## Batch updates

```python
from reactivity import signal, effect, batch

a = signal(0)
b = signal(0)

with effect(lambda: print(a.get(), b.get())):
    # prints 0 0 initially
    with batch():
        a.set(1)
        b.set(2)
        # signal values have already changed, but the effect has not run yet
    # prints 1 2 once on exit
```

Batching defers notifications, not assignments. Do not treat a batch as a database transaction: it does not roll back on exceptions. Avoid reading derived caches midway through a batch and assuming they reflect queued changes. Nested public `batch()` blocks flush their own callbacks on exit; batching is not a guarantee that an entire nested operation produces just one reaction. Subscriber ordering is not a public guarantee.

## Context isolation

```python
from reactivity import new_context, effect

ctx = new_context()
s = ctx.signal(0)

@effect(context=ctx)
def printer():
    print(s.get())

s.set(1)  # prints 1
printer.dispose()
```

Use the **same context** for sources, computations, and batches in a subsystem. Reading a signal from another context returns its value but does not subscribe the current computation to it. The module-level constructors support decorator configuration such as `@effect(context=ctx)`; `ctx.effect` is a constructor and takes the callable directly.

## Reactive collections

`reactive()` wraps mutable mappings, sets, and sequences, or creates copies for their immutable counterparts. It can also proxy ordinary objects with an instance `__dict__`. Tracking is fine-grained, but **shallow**: nested values are not automatically wrapped.

```python
from reactivity import reactive, effect

prices = reactive({"alice": 10, "bob": 20})
with effect(lambda: print(prices["alice"])):
    # prints 10 initially
    prices["bob"] = 25  # no output
    prices["alice"] = 12  # prints 12
```

Perform mutations through the reactive wrapper, not a retained reference to the underlying dictionary or list. See [Reactive Collections](../references/reactivity/collections.md) for limitations.

## Async

[`async_effect`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/async_primitives.py#L61 "hmr reactivity: AsyncEffect class — GitHub") and [`async_derived`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/async_primitives.py#L75 "hmr reactivity: AsyncDerived class — GitHub") are the async counterparts of `effect` and `derived`.

The existing sync mental model still applies, but the async variants have a few usage differences that are worth stating explicitly:

- `async_derived` is cached and lazy, just like `derived`, but you read it with `await value()`
- `async_effect` reruns side effects by scheduling work onto the current async runtime
- The default task factory supports `asyncio` and `trio`; pass `task_factory=` when you need task ownership to live in a specific `TaskGroup` or nursery

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

In a notebook with an active event loop, use `await main()` instead of `asyncio.run(main())`. The example waits for an event rather than relying on a fixed sleep to give propagation enough time.

If you need explicit task ownership, provide a custom `task_factory`:

```python
from asyncio import TaskGroup, sleep
from reactivity import async_effect, signal


async def main():
    tick = signal("tick")
    async with TaskGroup() as tg:

        @async_effect(task_factory=lambda fn: tg.create_task(fn()), call_immediately=False)
        async def printer():
            await sleep(0.01)
            print(tick.get())

        await printer()  # manually trigger the first run
        printer.dispose()
```

See the [async primitives reference](../references/reactivity/async_primitives.md) for the full API surface.

## Best practices

- Keep heavy init in stable modules or factories
- Prefer derived for computed values whose equality can suppress downstream work; use memoized when cache invalidation should propagate directly
- Batch related updates
- Use `new_context()` for isolated subsystems
- Restart for C extensions or global state changes
- Think of your code as a dependency graph: signals as sources, effects as consumers, derived as transformations
