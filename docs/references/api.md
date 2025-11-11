# API Reference

Core signatures and links. See [source](https://github.com/promplate/pyth-on-line/tree/main/packages/hmr) for full implementation.

## Reactive primitives (reactivity)

```python
from reactivity import (
    signal, state, effect, async_effect,
    derived, async_derived, memoized,
    batch, reactive, new_context
)
```

- [`signal`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py)(initial: T = None, check_equality: bool = True, *, context=None) -> Signal\
  Standalone observable. Methods: `get(track=True)`, `set(value)`, `update(fn)`.

- [`state`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py)(initial: T = None, check_equality: bool = True, *, context=None) -> descriptor\
  Descriptor for class attributes.

- [`effect`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py)(fn=None, /, call_immediately: bool = True, *, context=None) -> Effect | decorator\
  Side effects that auto-rerun on dependency changes.

- [`async_effect`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py)(fn=None, /, call_immediately: bool = True, *, context=None, task_factory=None) -> AsyncEffect\
  Async variant; supports asyncio/trio.

- [`derived`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py)(fn=None, /, check_equality: bool = True, *, context=None) -> Derived | decorator\
  Lazy computed value.

- [`async_derived`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py)(fn=None, /, check_equality: bool = True, *, context=None, task_factory=None) -> AsyncDerived\
  Async computed variant.

- [`memoized`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py)(fn=None, /, *, context=None) -> Memoized | decorator\
  Cached computation with dependency tracking.

- [`batch`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/_curried.py)(func=None, /, *, context=None) -> Batch | decorator | contextmanager\
  Group updates; effects run once after.

- [`reactive`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/collections.py)(value, check_equality=True, *, context=None) -> reactive container / proxy\
  Reactive dicts/sets/lists/objects with per-key tracking.

- [`new_context`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/context.py)() -> Context\
  Isolated reactive environment.

## Descriptor helpers

[`memoized_property`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/helpers.py), [`memoized_method`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/helpers.py),
[`derived_property`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/helpers.py), [`derived_method`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/helpers.py) — per-instance cached/derived attributes.

## HMR runtime

```python
from reactivity.hmr import cache_across_reloads, on_dispose, post_reload, pre_reload
from reactivity.hmr.core import patch_meta_path, ReactiveModule, SyncReloader, AsyncReloader
from reactivity.hmr.run import cli
```

- [`patch_meta_path`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/hmr/core.py)(includes=(".",), excludes=())\
  Install reactive module finder.

- [`ReactiveModule`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/hmr/core.py)(file: Path, namespace: dict, name: str)\
  Module wrapper with reactive globals.

- [`SyncReloader`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/hmr/core.py) / [`AsyncReloader`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/hmr/core.py)\
  Watch files and coordinate reload lifecycle.

- [`cache_across_reloads`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/hmr/core.py)(func) -> wrapped\
  Preserve cached values across reloads.

- [`pre_reload`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/hmr/core.py) / [`post_reload`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/hmr/core.py)\
  Global hooks for reload lifecycle.

- [`on_dispose`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/hmr/core.py)(func, **file**=None)\
  Per-module cleanup on reload.

- [`cli`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/hmr/run.py)(argv=None)\
  Entry for `hmr` console script.

## Notes

Python 3.12+. Runtime tracking: computations push to stack during execution; subscribables record dependencies. Preserves state for non-breaking changes; restart for C-level/protocol changes.

## Source

- Primitives: [`primitives.py`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/primitives.py), [`async_primitives.py`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/async_primitives.py),
  [`helpers.py`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/helpers.py)
- Containers: [`collections.py`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/collections.py)
- Context: [`context.py`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/context.py)
- HMR: [`hmr/core.py`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/hmr/core.py), [`hmr/run.py`](https://github.com/promplate/pyth-on-line/blob/main/packages/hmr/reactivity/hmr/run.py)
