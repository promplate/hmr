# Derived & Memoized

Lazy computations with dependency tracking and caching. Derived represents optimized data pipelines where values are cached if dependencies remain unchanged—computation is lazy and idempotent.

```python
from reactivity import signal, derived, memoized
```

[`derived`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) recomputes when read after invalidation. [`memoized`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) caches results and acts as a hard puller.

## Example

```python
s = signal(2)

square = derived(lambda: s.get() * s.get())
print(square())  # computes on first read

@memoized
def expensive():
    return sum(range(1_000_000))  # cached until invalidated
```

## Tips

- `derived` for lazy values; `memoized` for cached computations
- Both integrate with [`batch()`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) and contexts
- Use [`async_derived`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) for async computations
- Unlike effects, derived values are about returning processed data, not performing side effects

## See Also

- [Signals](signals.md) for the data sources derived depends on
- [Effects](effects.md) for side effects that may trigger derived recomputation
- [Advanced Reactivity](advanced.md) for async derived and patterns
