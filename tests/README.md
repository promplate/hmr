# Documentation regression checks

Run the focused checks with the same environment used to build the documentation:

```sh
uv run python -W error::RuntimeWarning -m unittest discover -s tests -v
```

The checks have also been run with `PYTHONPATH` pointing to `packages/hmr` in
`promplate/pyth-on-line` at commit `cdf0c6579ab58b90306eee99bf477b6d980aab05`,
as well as the published hmr 0.7.6.3 package, on Python 3.12.

The suite covers synchronous signals/state, dependency changes, effect disposal,
derived versus memoized notification, nested batches and stale reads inside a
batch, context isolation, shallow collection tracking, and asyncio tracking,
concurrent reads, task ownership and disposal. The custom-context collection
structural-tracking test records a current limitation rather than desired
behavior. Trio, Emscripten, and arbitrary overlapping invalidation races are not
covered.

`test_loader_docs.py` additionally covers reload hooks and disposal, tracked file reads, shared synchronous/async loader setup, and the CLI watcher flow. Integration examples were checked separately; these unit tests do not replace network/server integration testing.
