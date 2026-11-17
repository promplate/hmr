---
icon: lucide/cone
title: Reactive Module Loading
---

## Loading and invalidation

The behavior below is verified against `hmr==0.7.6.3`.

- Constructing a reloader installs a `ReactiveModuleFinder` at the front of `sys.meta_path`. By default it includes the working directory and excludes standard-library and installed-package paths.
- The finder handles file-backed Python modules and packages imported **after** installation. It does not retroactively replace ordinary modules already in `sys.modules`.
- A `ReactiveModule` executes its source into a reactive namespace. Reads of module attributes and imported names establish dependencies; a changed dependency can invalidate a dependent computation without restarting the interpreter.
- A watcher event invalidates known modules or notifies tracked file reads. Changes are processed in an HMR-context batch, between the pre- and post-reload callbacks.

`includes` and `excludes` are iterables of paths; use `includes=("src",)` rather than a bare string. These configure what can be loaded or tracked reactively. They are not a security boundary.

!!! note "Watch loops and entry execution are separate"

    `start_watching()` only consumes file events. `keep_watching_until_interrupt()` also installs the reactive effect that runs the entry file. Prefer the CLI or context-manager API instead of calling internal loading properties yourself.

!!! warning "Not a fresh module or an atomic deployment"

    Module execution reuses its namespace. Do not assume deleted names, existing object instances, or external side effects disappear on reload. Disposal callbacks run before a successfully parsed replacement executes, not after that execution is known to succeed. Syntax errors are reported without executing the replacement; arbitrary runtime failures are not transactional rollbacks.

# `#!py from reactivity.hmr.core import *`

::: reactivity.hmr.core
