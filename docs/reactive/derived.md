---
icon: lucide/squircle-dashed
---

# Derived

!!! note "Verification scope"

    The examples and semantics on this page were checked against hmr 0.7.6.3 and the [current core source](https://github.com/promplate/pyth-on-line/tree/cdf0c6579ab58b90306eee99bf477b6d980aab05/packages/hmr/reactivity). This is a source and runtime check, not a claim of human review.

A `derived` value is a calculation based on one or more signals. It's both **lazy** and **cached**.

- **Lazy**: The calculation runs when its value is pulled, either by your code or by an active downstream consumer.
- **Cached**: If its underlying signals haven't changed, the `derived` value won't re-run the calculation. It will return a cached result.

When a signal dependency changes, the derived value becomes dirty. Without an active downstream consumer, it waits for the next read. If an effect depends on it, directly or through other derived values, the engine pulls the new value during propagation. The effect only reruns if the derived result changes (with the default equality check). Thus "lazy" does not mean that an observed derived value always waits for an explicit call in your code.

```python
from reactivity import signal, derived, memoized, effect
```

[`derived`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/primitives.py#L262 "hmr reactivity: Derived class — GitHub") is for **lazy, pull-based** computations. It recalculates when read, including reads forced by downstream consumers.

[`memoized`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/helpers.py#L10 "hmr reactivity: Memoized class — GitHub") is the function-cache variant. It stores the last result, becomes stale when its dependencies change, and recomputes on the next call.

## Example

```python
from reactivity import signal, derived

s = signal(0)

@derived
def f():
    print("expensive computing ...")
    return s.get() * 2
```

Unlike effect which runs immediately when declared, nothing will happen until you call `f()`:

```python
print(f())
# expensive computing ...
# 0
print(f())
# 0
```

Multiple calls won't re-run, but will return the cached value. Because when the data source is unchanged, idempotent data pipelines should return the same result.

But if you call `s.set(...)` and then call `f()` again, f will re-run once:

```python
s.set(1)
print(f())
# expensive computing ...
# 2
print(f())
# 2
```

## Derived versus memoized

Both wrap a **zero-argument** callable and cache one result based on the reactive values read during execution. `memoized` is not an argument-keyed cache like `functools.lru_cache`.

The difference is notification: `derived` compares its recomputed result and can stop propagation when it is unchanged. `memoized` notifies subscribers as soon as its cache becomes stale, without comparing the next result. An effect reading a memoized value therefore reruns even when recomputation returns an equal value.

For example, if both calculate `s.get() // 2`, changing `s` from `2` to `3` does not rerun an effect subscribed only to the derived value, but does rerun one subscribed to the memoized value.

Use reactive reads inside these callables. A constant calculation has no dependencies to invalidate its cache and produces a runtime warning by default. Ordinary non-reactive inputs do not invalidate either cache automatically.

## Tips

- `derived` for lazy computed values; `memoized` for function-style caching around reactive reads
- Both integrate with [`batch()`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/primitives.py#L218 "hmr reactivity: Batch class — GitHub") and contexts
- Use [`async_derived`](https://github.com/promplate/pyth-on-line/blob/hmr/v0.7.6/packages/hmr/reactivity/async_primitives.py#L75 "hmr reactivity: AsyncDerived class — GitHub") for cached async computations; read them with `await value()`
- Unlike effects, derived values are about returning processed data, not performing side effects

## See Also

- [Signals](signals.md) for the data sources derived depends on
- [Effects](effects.md) for side effects that may trigger derived recomputation
- [Advanced Reactivity](advanced.md) for async derived and patterns
