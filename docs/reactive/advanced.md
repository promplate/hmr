# Advanced Reactivity

HMR uses **push-pull reactivity**: signals push notifications when updated, but derived values are lazy and only recompute when read (pulled) or when a hard puller (like an effect) forces evaluation.

## Core primitives

```python
from reactivity import signal, state, effect, derived, memoized, batch, new_context, reactive
```

- [`signal`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) / [`state`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py): observables
- [`effect`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py): side effects
- [`derived`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py): lazy computed
- [`memoized`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py): cached with hard pull
- [`batch`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py): group updates
- [`new_context`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/context.py): isolated environment
- [`reactive`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/collections.py): reactive containers

## Patterns

### 1. Stable resources

Keep DB clients, ML models, sockets in modules you rarely edit or behind factories.

### 2. Derived for computed values

```python
s = signal(1)
square = derived(lambda: s.get() ** 2)  # lazy
```

### 3. Effect for side effects

```python
@effect
def logger():
    print(square())
```

### 4. Memoized for expensive calls

```python
@memoized
def expensive():
    return sum(range(1_000_000))
```

### 5. Batch updates

```python
a = signal(0)
b = signal(0)

with batch():
    a.set(a.get() + 1)
    b.set(b.get() + 2)
```

### 6. Context isolation

```python
ctx = new_context()
s = signal(0, context=ctx)
@effect(context=ctx)
def _():
    print(s.get())
```

### 7. Reactive containers

[`reactive()`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/collections.py) creates reactive dicts, sets, lists, or object proxies with per-key/index tracking.

## Async

[`async_effect`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) and [`async_derived`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py) support asyncio/trio via pluggable task factories.

## Best practices

- Keep heavy init in stable modules or factories
- Prefer derived/memoized over manual caching
- Batch related updates
- Use `new_context()` for isolated subsystems
- Restart for C extensions or global state changes
