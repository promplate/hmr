---
icon: lucide/circle-dashed
---

# Signals

!!! note "Verification scope"

    The examples and semantics on this page were checked against hmr 0.7.6.3 and the [current core source](https://github.com/promplate/pyth-on-line/tree/cdf0c6579ab58b90306eee99bf477b6d980aab05/packages/hmr/reactivity). This is a source and runtime check, not a claim of human review.

Observable values with automatic dependency tracking. Signals are the foundation of reactive programming, evolving from the [observer pattern](https://en.wikipedia.org/wiki/Observer_pattern "Observer pattern - Wikipedia") where data sources notify observers of changes.

> The traditional observer pattern requires manual binding of subject and observer **dependencies**. Reactive programming implements automatic tracking of these dependencies on top of that.
>
> A useful mental model is the call stack: if A calls B, then A depends on B. HMR starts from that intuition, but the real implementation is a little broader.
>
> In synchronous code, dependency tracking often looks like "who is currently on the stack". In asynchronous code, HMR keeps that tracking alive through its reactivity context, so dependencies can still be collected across `await` boundaries.

```python
from reactivity import signal, state
```

[`signal`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/primitives.py#L91 "hmr reactivity: Signal class - GitHub") creates standalone observables. [`state`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/primitives.py#L181 "hmr reactivity: State class - GitHub") is a descriptor for class attributes.

## Usage

In HMR, there are two primitives: Signal and Effect. Signal is like a data source:

```python
from reactivity import signal

s = signal(0)  # initial value is 0

print(s.get())  # use .get() to get the value of a signal

s.set(1)  # set its value to 1

print(s.get())

s.update(lambda x: x + 1)  # update using a function
```

This will print out 0 and then 1. As you can see, Signal is just a data source that tracks changes.

## State descriptor

For class attributes, you can use the `state` descriptor:

```python
class Counter:
    value = state(0)


c = Counter()
c.value = 1  # notifies effects reading c.value
```

Each instance gets its own signal. Reading `Counter.value` on the class returns the descriptor, not an instance's value. Prefer immutable defaults; `state` does not copy a mutable initial value for each instance.

## Equality and mutation

By default, setting an equal value does not notify subscribers. `set()` and `update()` return `True` when they notify, and `False` when the equality check suppresses the update. Use `signal(value, check_equality=False)` when every assignment should notify.

A signal observes assignments, not mutations inside its value. For example, appending to a list returned by `s.get()` does not notify. Create a new value with `s.update(lambda items: [*items, new_item])`, or use a [reactive collection](../references/reactivity/collections.md). Setting the same mutated object back is also suppressed by the default identity/equality check.

## Tips

- `get(track=False)` reads without subscribing to changes
- `update(updater)` modifies the signal using a function
- [`batch()`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/primitives.py#L218 "hmr reactivity: Batch class - GitHub") groups updates to reduce recomputations
- Use `state` for instance attributes, `signal` for standalone values
- Signals automatically track dependencies in the current reactive execution context, eliminating manual observer binding

## See Also

- [Effects](effects.md) for side effects that react to signals
- [Derived](derived.md) for computed values based on signals
- [Advanced Reactivity](advanced.md) for patterns and best practices
