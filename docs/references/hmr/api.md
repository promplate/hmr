---
icon: lucide/trash
title: Legacy HMR API
---

!!! warning "Legacy embedding API"

    This page describes the API shipped in `hmr==0.7.6.3`. It is a low-level integration surface, not a promise about a future replacement or release date. Prefer the CLI or a framework integration when possible.

## Running a reloader in an existing program

Create the reloader **before** importing application modules that should be reactive. Run from the application directory so the default `includes=(".",)` covers those modules.

```python
from reactivity.hmr.api import SyncReloaderAPI

with SyncReloaderAPI("app.py"):
    input("Edit app.py, then press Enter to stop.\n")
```

The entry file runs when the context is entered, before the watcher starts. It must return control; an indefinitely blocking server loop at module scope prevents startup from completing. The synchronous context runs the watcher in a background thread and joins it on exit. `AsyncReloaderAPI` also supports synchronous and asynchronous contexts; an asynchronous context awaits the watcher task during shutdown.

Keep a reloader variable separately if needed. Do not rely on the value of `with ... as value`: these legacy context managers return a `super()` proxy, not the reloader instance.

!!! warning "Cleanup is not a process reset"

    Context exit stops the watcher, disposes the entry effect/loading computation, and restores `sys.modules["__main__"]`. In `0.7.6.3`, it does not remove the installed import finder, unload imported modules, or run all application disposal callbacks. Own application resources with an explicit `try/finally` or context manager. Avoid repeatedly creating reloaders as a substitute for process isolation.

# `#!py from reactivity.hmr.api import *`

::: reactivity.hmr.api
