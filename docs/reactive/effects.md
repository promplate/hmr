# Effects

Side effects that auto-rerun when dependencies change. Effects subscribe to data sources and automatically execute when signals they depend on update—unlike simple function calls, effects react to data changes.

```python
from reactivity import effect
```

Runs immediately by default; set `call_immediately=False` to defer. Dispose with `.dispose()` or context manager.

## Example

```python
from reactivity import signal, effect

s = signal(0)

@effect
def logger():
    print(s.get())
```

## Tips

- [`memoized`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) caches computations as dependencies; `effect` runs side effects
- [`batch()`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) groups updates to run effects once
- Keep effects small and idempotent
- Use [`async_effect`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) for async tasks
- Dispose to avoid leaks
- Effects are about "what to do with data" rather than returning processed values
