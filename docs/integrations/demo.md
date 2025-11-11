# Demo — minimal HMR example

A tiny, framework-free demo that shows HMR behavior with plain Python modules.

Quick facts

- Location: `examples/demo/`
- Entry point: `entry.py`
- Purpose: show module reloads, fine‑grained dependency tracking, and state preservation.

Run it

```sh
cd examples/demo
pip install -e .
hmr entry.py
```

What to watch for

- Edit `a.py` or `b.py` and save: HMR reruns the changed module and any dependents.
- Long‑lived state (e.g. objects created in modules you don’t edit) is preserved when possible.
- Only affected names are re-evaluated thanks to runtime dependency tracking.

Tips

- Keep heavy initialization in modules you rarely edit, or behind a factory so it can be re-created explicitly.
- Use small pure functions for logic that you expect to swap frequently.
- If behavior looks inconsistent after a reload, check for global mutable state, double-registration, or ABI/protocol changes.

See also: `docs/getting-started/quick-start.md` and `docs/reactive/advanced.md`.
