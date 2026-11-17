# Deterministic causal isolation (2026-11-17)

Uses installed reactivity hmr 0.7.6.3 and real PipeReloader/watchfiles subprocess events. Comparison daemon source is baseline `9b586ea` vs #174 `c22311a`. No Uvicorn or #173 code is needed to produce either failure.

`batch_barrier.py`: explicit BaseReloader.on_changes enters a main-thread Batch; injected notify observation writes main.py; actual automatic daemon receives event and enters another Batch; barrier holds daemon there until main exits. Baseline shared `HMR_CONTEXT.batches` becomes [A, B]; main pops B and asserts `last is self`. Patched daemon handoff stops the second event source, so main exits A normally.

`sniffio_case.py`: asyncio AsyncEffect tracks main.py, explicit BaseReloader is constructed, real file change invokes the still-running daemon. Baseline executes AsyncEffect.trigger/default_task_factory on `hmr-daemon`, with no async event-loop context, reproducing AsyncLibraryNotFoundError. Patched handoff stops daemon; no async scheduling occurs on that thread.

Both scripts require startup NO_HMR_DAEMON=1, then deliberately load selected daemon source from argument path after removing the disabled pth-imported module from sys.modules. This ensures exact selected source, rather than accidentally reusing the installed package. Arguments are directories containing an hmr_daemon package. CWD must contain main.py before process starts. Baseline source package differs only at posix/main.py from patched checkout.

Blame: a1d3ee69ce17d9e802ccfd55b3cba109e3bbb7ef, posix/main.py:111 sets thread-local state.disabled=True on main thread; explicit reloaders constructed on that same thread skip wrapper's shutdown branch at lines44–47. The worker exemption belongs on the worker thread, not main thread.

These are controlled mechanism tests, not claims that every historical CI error instance had an identical instruction interleaving. Real Uvicorn source-event tracing is separately being collected by the other investigator.

## Real Uvicorn confirmation

The uvicorn subdirectory traces actual BaseReloader.on_changes calls from both real file watchers. For the original daemon case only, it delays main-thread delivery until the daemon callback finishes, making the daemon-first order explicit. It does not inject invalidation or invoke the daemon callback itself.

Run `python diagnostics/causal/uvicorn/prepare.py` from this checkout after installing workspace packages. Then, with the workspace venv Python and startup `NO_HMR_DAEMON=1`, run `diagnostics/causal/uvicorn/run.py base base`, `base fixed`, `base disabled`, `patched base`, `patched fixed`, `patched disabled`.

Original daemon + either Uvicorn version emits the same source-file event from hmr-daemon/PipeReloader and MainThread/Reloader, and reports AsyncLibraryNotFoundError. Fixed or disabled daemon + either Uvicorn version emits only the MainThread event and no traceback. Logs are preserved here. RESULT True for original-daemon cases means the *expected failure was observed*, not that the daemon worked correctly.

The former functional regression could pass despite stderr containing these errors; #173's follow-up tests now isolate the explicit runtime, restore source rewriting, and check logs. No claim is made that #173 cannot affect timing under an already-broken dual-watcher setup.
