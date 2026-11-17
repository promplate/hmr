---
icon: lucide/squircle
---

# Effects

!!! note "Verification scope"

    The examples and semantics on this page were checked against hmr 0.7.6.3 and the [current core source](https://github.com/promplate/pyth-on-line/tree/cdf0c6579ab58b90306eee99bf477b6d980aab05/packages/hmr/reactivity). This is a source and runtime check, not a claim of human review.

Side effects that automatically rerun when their dependencies change. Effects subscribe to signals and derived values, executing whenever those dependencies update.

```python
from reactivity import effect
```

Unlike a simple `print(s.get())`, when the value of `s` changes, related Effects will rerun.

The handler must be callable without arguments. It can be a lambda or a decorated function.

## Example

```python
from reactivity import signal, effect

s = signal(0)

effect(lambda: print(s.get()))  # like above, this would print 0
```

Now, when the value of `s` changes, the related effects will rerun. For example:

```python
s.set(1)
```

This will print out 1, even though you didn't explicitly call `print(s.get())` again.

## Decorator usage

```python
@effect
def _():
    print(s.get())
```

Runs immediately by default. Dependencies are the reactive values actually read during each run, so conditional branches can change subscriptions. A synchronous effect runs during change propagation, before `set()` returns, unless updates are batched.

### Deferred start and cleanup

`call_immediately=False` skips the initial run. It does **not** collect dependencies yet, so updating a signal cannot start it. Call the effect once to establish subscriptions:

```python
from reactivity import effect, signal

s = signal(0)
printer = effect(lambda: print(s.get()), call_immediately=False)
s.set(1)  # no output: printer has not subscribed yet
printer()  # prints 1 and subscribes
s.set(2)  # prints 2
printer.dispose()
s.set(3)  # no output
```

A context manager also removes subscriptions on exit:

```python
with effect(lambda: print(s.get())):
    s.set(4)
# The effect no longer subscribes to s.
```

Disposal removes subscriptions; it is not a permanent disable switch. Calling the effect again runs it and subscribes again. It also does not undo external side effects, so close files, connections, and other resources explicitly.

## Tips

- [`memoized`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/helpers.py#L10 "hmr reactivity: Memoized class — GitHub") caches computations as dependencies; `effect` runs side effects
- [`batch()`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/primitives.py#L218 "hmr reactivity: Batch class — GitHub") defers notifications until a batch flush; see the batching caveats in [Advanced Reactivity](advanced.md#batch-updates)
- Keep effects small and idempotent
- Use [`async_effect`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/async_primitives.py#L61 "hmr reactivity: AsyncEffect class — GitHub") for async side effects; it schedules work onto the current async runtime or your custom `task_factory`
- Dispose to avoid leaks
- Effects are semantically closer to "what to do with the data source" rather than "return processed data"

## See Also

- [Signals](signals.md) for observable data sources
- [Derived](derived.md) for cached computations
- [Advanced Reactivity](advanced.md) for async effects and patterns
