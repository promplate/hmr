# API Reference

Core signatures. See package sources for full usage examples.

## Reactive primitives

```python
from reactivity import (
    signal, state, effect, async_effect,
    derived, async_derived, memoized,
    batch, reactive, new_context
)
```

- `signal(initial, check_equality=True, context=None)` - Standalone observable. Methods: get(), set(), update().
- `state(initial, check_equality=True, context=None)` - Descriptor for class attributes.
- `effect(fn, call_immediately=True, context=None)` - Side effects that auto-rerun on dependency changes.
- `derived(fn, check_equality=True, context=None)` - Lazy computed value.
- `memoized(fn, context=None)` - Cached computation with dependency tracking.
- `batch(func=None, context=None)` - Group updates; effects run once after.
- `reactive(value, check_equality=True, context=None)` - Reactive containers with per-key tracking.
- `new_context()` - Isolated reactive environment.

Descriptor helpers: `memoized_property`, `derived_property` etc.

## HMR runtime

```python
from reactivity.hmr import cache_across_reloads, on_dispose, post_reload, pre_reload
from reactivity.hmr.core import patch_meta_path, ReactiveModule
from reactivity.hmr.run import cli
```

- `patch_meta_path(includes, excludes)` - Install reactive module finder
- `ReactiveModule(file, namespace, name)` - Module wrapper with reactive globals
- `SyncReloader` / `AsyncReloader` - Watch files and coordinate reload lifecycle
- `cache_across_reloads(func)` - Preserve cached values across reloads
- `pre_reload` / `post_reload` - Global hooks for reload lifecycle
- `on_dispose(func, file=None)` - Per-module cleanup on reload
- `cli(argv=None)` - Entry for hmr console script

## Notes

Python 3.12+. Runtime tracking: computations push to stack during execution; subscribables record dependencies. Preserves state for non-breaking changes; restart for C-level/protocol changes.

## Source

Package available on PyPI. Full docs at <https://hmr.promplate.dev/>
