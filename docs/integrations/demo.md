---
icon: lucide/terminal
---

# Demo — minimal HMR example

The [repository demo](https://github.com/promplate/hmr/tree/docs/examples/demo) contains `entry.py`, `a.py`, `b.py` and `common.py`. It uses Rich to print which modules execute.

From a checkout of the `docs` branch:

```sh
cd examples/demo
uv run hmr entry.py
```

Or install `hmr` and `rich` in your environment and run `hmr entry.py` from the same directory.

Initially, `a.py` exports `a = 1`, `b.py` computes `b = 2`, and the entry prints `a + b = 3`. Change `a = 1` to `a = 10` and save. The entry prints `a + b = 12`; `b.py` need not run again because its dependencies did not change.

HMR tracks reads of module attributes. This can avoid re-executing unrelated modules, but it does not execute only individual changed statements: a module that reloads executes its top-level code again. Unchanged imported names can also prevent downstream propagation when their values compare equal.

Objects in unaffected modules, such as `common.console` in this edit, can remain alive. This is not a general guarantee that all application state survives reloads. Keep initialization and cleanup explicit.

See the [quick start](../getting-started/quick-start.md) and [advanced reactive topics](../reactive/advanced.md).
