# vllm-hmr

Hot module replacement for a vLLM server: edit one Python file in the request path and the next request runs the new code, without restarting the server or reloading model weights.

Scope is deliberately narrow. This is not a general "reload any vLLM module" tool — see [Capability limits](#capability-limits) before relying on it.

## Install

```bash
pip install vllm-hmr
```

vLLM itself is **not** bundled. The verified release is 0.28.0 (`pip install "vllm==0.28.0"`); other versions are not supported by the current evidence. The wrapper `exec`s the official `vllm` executable it finds on `PATH`.

## Usage

```bash
vllm-hmr serve facebook/opt-125m --port 8000
```

That is a normal `vllm serve` — same PID, signals, and exit code, because the wrapper `exec`s the official CLI in place — with HMR enabled. Your vLLM arguments are forwarded verbatim; the wrapper does not reimplement vLLM's parser.

HMR needs a source tree it can watch. An editable/source vLLM checkout is detected automatically. A regular site-packages install cannot be edited in place, so there it is an error that names the fix rather than copying anything on your behalf:

```bash
vllm-hmr serve facebook/opt-125m --hmr-source-root ~/src/vllm
```

To run without HMR at all:

```bash
vllm-hmr --hmr-disabled serve facebook/opt-125m   # equivalent to `vllm serve facebook/opt-125m`
```

### What the wrapper adds to `serve`

On `serve` — and only on `serve` — two real vLLM 0.28 flags are appended:

| Flag | Value | Why |
|------|-------|-----|
| `--middleware` | `vllm_hmr.runtime.middleware.HMRBoundaryMiddleware` | publishes changes between requests, never inside one |
| `--worker-extension-cls` | `vllm_hmr.runtime.worker.HMRWorkerExtension` | lets each worker process reload its own modules |

If you pass either flag yourself (in `--flag value` or `--flag=value` form), yours wins and ours is dropped: no flag is ever supplied twice. Tokens after `--` are left alone, and injection lands before them. Other subcommands (`chat`, `complete`, `bench`, …) are forwarded completely untouched, with no HMR environment exported.

### HMR options

Each has an environment variable equivalent; CLI options win.

| CLI flag | Environment variable | Purpose |
|----------|----------------------|---------|
| `--hmr-source-root PATH` | `HMR_VLLM_SOURCE_ROOT` | vLLM source tree to watch; required unless an editable checkout is detected |
| `--hmr-manifest PATH` | `HMR_VLLM_MANIFEST` | use/verify an existing manifest instead of generating one |
| `--hmr-runtime SPEC` | `HMR_VLLM_RUNTIME` | replace the packaged runtime with your own `module:callable`; it owns its own scope, so only the source root's existence is checked. The spec is resolved before launch — an unimportable module, a missing attribute, or a non-callable target is a usage error, because `site` would otherwise report it as one line of stderr and start vLLM without HMR |
| `--hmr-disabled` | `HMR_VLLM_DISABLED` | plain `vllm` launch: no injection, no flags added (any non-empty value). An activation already present in the environment is removed, so this also opts out inside a shell or script that exported `HMR_VLLM_*` earlier |
| `--hmr-print-env` | — | print the computed environment and exec argv, then exit |

Three runtime-only controls are environment variables rather than CLI arguments:

- `HMR_VLLM_RPC_TIMEOUT_S` bounds the wait for worker publication RPCs in seconds (default `30`; non-positive or invalid values use the default). When a fan-out exceeds this deadline, the middleware returns HTTP 503 to the current request but preserves the background task so the next request can read its outcome instead of guessing. While the task is running, inference requests stay at 503 (fail-closed); health and metrics requests passthrough without entering the boundary lock, so they are never blocked. Once the task finishes, its result decides whether to serve or requeue: success permits inference, failure requeues the published records and continues returning 503 until a later boundary succeeds.
- `HMR_VLLM_MAX_WATCHER_RESTARTS` limits consecutive watcher recovery attempts (default `3`; a successful healthy boundary resets the budget). Recovery rescans content hashes so edits made while the watcher was down are not lost; exhaustion marks HMR unavailable and leaves pending edits undrained.
- `HMR_VLLM_EVENT_LOG_DIR` makes each process append its runtime events to `<dir>/hmr-events-<pid>.jsonl` (off by default). This is the only way to observe a queued change out of band: publication happens at request boundaries, so an HTTP request that asks whether something is queued is itself the boundary that publishes it. Reading the file instead leaves the server untouched — which is how the CPU smoke waits for every watcher before sending the one request that must publish.

## How it works

1. The wrapper resolves the source root, appends the two vLLM flags, exports `HMR_VLLM_*`, and prepends its own `sitecustomize` directory to `PYTHONPATH`.
2. It `execve`s the official `vllm`. Because the shim sits on `PYTHONPATH`, CPython imports it during `site` initialization — before vLLM or torch. Any `sitecustomize` it shadows is chained first, so environments that rely on their own keep working.
3. The shim invokes the runtime, which validates the manifest, installs the [pyth-on-line](https://github.com/promplate/pyth-on-line) reactive import hook for the in-scope files only, and starts a watcher.
4. Subprocesses inherit the environment, so workers get the same early injection. vLLM's short-lived model-registry inspector is detected and left watcher-free; `HMR_VLLM_SKIP=1` suppresses injection for any process that must not have it.
5. On a file change the watcher only queues the change. Publication happens in the middleware, between requests: the API process reloads, then asks the workers to do the same via RPC on eligible inference request boundaries (paths beginning with `/v1/`). Every other path — health, metrics, anything outside `/v1/` — is passed through before the boundary lock is taken, so it neither fans out nor waits on one that is stuck. A request in flight blocks publication, so no single request sees two versions of the same module. Request boundaries are serialised against each other, so two requests arriving together cannot both decide that nothing is in flight; the requests themselves stay concurrent.

The fan-out is bounded by `HMR_VLLM_RPC_TIMEOUT_S` on the API side, because vLLM 0.28's `UniProcExecutor.collective_rpc` accepts a `timeout` and never reads it. Exceeding that deadline is not a cancellation: the fan-out keeps running and is handed to the next request boundary, which reads its real result — an abandoned fan-out could otherwise publish in a worker at an instant the API process no longer tracks, which is the version skew the boundary exists to prevent. Until that outcome is known, `/v1/` requests are refused with a structured 503 rather than served across an unverified worker set, and a client disconnecting mid-fan-out is handled the same way. A confirmed failure requeues what the API published so a later boundary re-attempts it; only a confirmed success serves inference.

## Scope

Two files are reloadable, and a manifest must name exactly that set in every one of its fields (`files`, `reactive_paths`, `auto_paths`, `forced_dependents`) — it can neither widen it nor narrow it:

- `vllm/renderers/inputs/preprocess.py` — the provider that is actually replaced
- `vllm/v1/engine/async_llm.py` — its real direct `from … import` consumer, re-executed so the new function object reaches the live request path

A manifest recording the source root, per-file SHA-256, and reactive paths is generated at startup, or verified if you pass `--hmr-manifest`. A missing file, a stale hash, a mismatched source root, a missing or extra path or module, a missing field, a wrongly typed field, unparsable JSON, or an unknown schema version fails immediately, before anything is watched — and a `--hmr-manifest` is verified by the wrapper too, since a manifest first read inside `sitecustomize` would fail as one stderr line in vLLM's startup output, i.e. as silently missing HMR. Narrowing is rejected for the same reason as widening: a manifest that watches nothing, or that swaps the provider without re-executing its consumer, installs a runtime that looks healthy and serves stale code. Source files are never modified by this package.

Publication also verifies the source root against reality: an in-scope file the live process did not import from that root is **rejected**, not reported as published. This is what catches a source root that is only a copy of an installed vLLM — the process is running the installed one, so no edit to the copy can reach it.

If a target or forced dependent raises while reloading, the runtime restores the pre-publication name bindings and keeps the existing module objects. This rollback is intentionally shallow: in-place mutations to an already-bound mutable object cannot be undone generically, so reloadable modules must not perform such side effects at import time.

## Capability limits

The evidence behind this package is one smoke test: vLLM **0.28.0+cpu** on the official `vllm/vllm-openai-cpu:v0.28.0-x86_64` image, one server instance with separate API and worker processes, one request path. A stacked example PR will add `examples/vllm-cpu-hmr`, which launches through this CLI and asserts that the target function is replaced while API and worker PIDs, the model object, and its parameter pointers stay unchanged.

Verified:

- ✅ One Python function in the request path, replaced live
- ✅ Manifest-listed files with an explicit direct dependent
- ✅ API and worker process identity, model object, and parameter pointers unchanged
- ✅ Publication deferred while a request is in flight

Not verified, not claimed:

- ❌ Any vLLM version other than 0.28.0+cpu, and any CUDA/GPU build
- ❌ Multi-rank or multi-node atomic publication (workers are told to reload one after another; there is no cross-rank transaction)
- ❌ Model class, weight, or `nn.Module` hot reload
- ❌ Scheduler, config, entrypoint, or route replacement
- ❌ Triton/CUDA kernel, `torch.compile`, or CUDA graph invalidation
- ❌ Long-lived production safety

Widening the scope is not a configuration change: it requires new evidence that the target survives in-place replacement.

## License

MIT

## Links

- [Homepage](https://github.com/promplate/hmr)
- [Documentation](https://hmr.promplate.dev)
- [Repository](https://github.com/promplate/hmr/tree/HEAD/packages/vllm-hmr)
