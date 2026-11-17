---
icon: lucide/cone
title: Reactive File System
---

## Tracked file reads

With the HMR loader active, synchronous file reads performed during a tracked computation can become dependencies. For example, run this file with `hmr app.py` and edit `message.txt`:

```python
from pathlib import Path

print(Path("message.txt").read_text())
```

Create `message.txt` before starting. In `hmr==0.7.6.3`, the audit hook tracks qualifying `open` events while an HMR computation is active and the path passes a loader's include/exclude filter. A later file-change notification invalidates the dependent computation. No decorator is required for this module-level read.

This is not general filesystem virtualization. Do not assume directory listings, reads outside an active tracked computation, excluded paths, or arbitrary file-descriptor access become dependencies. The watcher currently ignores deletion events; removal alone is not a reliable trigger to re-run a file reader.

`track(Path(...))` and `notify(Path(...))` are low-level dependency operations, not file readers or watcher setup. Use matching resolved paths when calling them manually.

# `#!py from reactivity.hmr.fs import *`

::: reactivity.hmr.fs
