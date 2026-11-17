---
icon: lucide/cone
title: HMR CLI
---

## Command-line usage

```bash
hmr app.py arg1 arg2
hmr -m mypackage arg1 arg2
python -m reactivity.hmr app.py
```

`hmr app.py` executes a file as `__main__`. `hmr -m mypackage` resolves the module, or the package's `__main__.py`, and preserves its package context so relative imports work. Arguments after the entry point are available through `sys.argv[1:]`. A directory entry is also accepted if it contains `__main__.py`; use `-m` when package-relative imports matter.

Run from the project directory: the default reactive include path is the working directory. The CLI runs the entry under an HMR effect and then watches for changes without restarting the interpreter. Press ++ctrl+c++ to stop.

The entry module must finish its top-level execution before watching begins. For an indefinitely running web server, use a supported framework integration rather than wrapping a blocking server launch in `hmr app.py`.

These commands and package-relative imports were verified with `hmr==0.7.6.3`, including a real watcher update from an imported module. `--help` prints usage; missing entries or unresolvable modules produce an error. HMR does not provide a production deployment or process-supervision contract.

# `#!py from reactivity.hmr.run import *`

::: reactivity.hmr.run
