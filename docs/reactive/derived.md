# Derived & Memoized

Lazy computations with dependency tracking.

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
