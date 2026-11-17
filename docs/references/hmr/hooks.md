---
icon: lucide/cone
title: HMR Hooks
---

## Hook lifecycle

The behavior below is verified against `hmr==0.7.6.3`.

```python
from reactivity.hmr import on_dispose

resource = open("output.log", "a")

@on_dispose
def close_resource():
    resource.close()
```

Place this code in a module managed by HMR. `on_dispose` looks up the reactive module using the caller's `__file__` (or its explicit file argument); it is not a general-purpose shutdown decorator for ordinary imported modules.

When that module is loaded again, HMR first parses and compiles the replacement, calls its previously registered disposal callbacks, clears them, and then executes the new source. A syntax error therefore does not dispose the currently loaded module. Exceptions from individual disposal callbacks are suppressed. These callbacks are **not guaranteed to run when the watcher stops or the process exits**; provide separate final-shutdown cleanup where needed.

### Reload-wide callbacks

`pre_reload` and `post_reload` register synchronous, zero-argument callbacks around a reload batch. They also run around the initial entry execution. A pre-hook registered by the entry file itself is too late for that first pre phase. A post-hook means the processing phase ended, not that every changed module executed successfully.

Callbacks are registered by function **name**, not by module or function identity. Registering another callback with the same name replaces the previous one. Use distinct names across modules. `use_pre_reload` and `use_post_reload` register only for the duration of a `with` block and remove that name on exit; they do not restore a previous callback with the same name.

Hooks are called directly, without awaiting returned values. Use ordinary `def` callbacks, not `async def`. Unlike module-disposal callbacks, exceptions from pre/post hooks are not suppressed by the hook dispatcher.

# `#!py from reactivity.hmr.hooks import *`

::: reactivity.hmr.hooks
