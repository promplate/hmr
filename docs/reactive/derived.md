# Derived

HMR also provides a primitive, Derived, which represents a data processing pipeline. It is highly optimized—if the signals it depends on haven't changed after computation, its value will be cached. Its computation is lazy. (Relatively speaking, effect is not cached and it's not recommended to return values in effects, because Effect is semantically closer to "what to do with the data source" while Derived is "return processed data").

```python
from reactivity import signal, derived, memoized
```

[`derived`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py#L39 "hmr reactivity: _curried.py — GitHub") recomputes when read after invalidation. [`memoized`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py#L75 "hmr reactivity: _curried.py — GitHub") caches results and acts as a hard puller.

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

## Tips

- `derived` for lazy values; `memoized` for cached computations
- Both integrate with [`batch()`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py#L135 "hmr reactivity: _curried.py — GitHub") and contexts
- Use [`async_derived`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py#L123 "hmr reactivity: _curried.py — GitHub") for async computations
- Unlike effects, derived values are about returning processed data, not performing side effects

## See Also

- [Signals](signals.md "Signals") for the data sources derived depends on
- [Effects](effects.md "Effects") for side effects that may trigger derived recomputation
- [Advanced Reactivity](advanced.md "Advanced Reactivity") for async derived and patterns
