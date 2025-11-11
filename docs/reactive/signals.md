# Signals & State

Observable values with automatic dependency tracking.

```python
from reactivity import signal, state
```

[`signal`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) creates standalone observables. [`state`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) is a descriptor for class attributes.

## Usage

```python
s = signal(0)
print(s.get())    # read (tracks when used in computations)
s.set(1)          # notify if changed
s.update(lambda v: v + 1)
```

## State descriptor

```python
class Counter:
    value = state(0)

c = Counter()
c.value = 1       # notifies effects reading Counter.value
```

## Tips

- `get(track=False)` reads without subscribing
- [`batch()`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) groups updates to reduce recomputations
- Use `state` for instance attributes, `signal` for standalone values
