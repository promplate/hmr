# sglang-hmr

Hot module replacement for an SGLang server: edit one Python file in the request path and the next request runs the new code, without restarting the server or reloading model weights.

Scope is deliberately narrow. This is not a general "reload any SGLang module" tool — see [Capability limits](#capability-limits) before relying on it.

## Install

```bash
pip install sglang-hmr
```

SGLang itself is **not** bundled; install it separately (`pip install sglang`). The wrapper `exec`s the official `sglang` executable it finds on `PATH`.

## Usage

```bash
sglang-hmr serve facebook/opt-125m --device cpu --port 31000
```

That is a normal `sglang serve` — same PID, signals, and exit code, because the wrapper `exec`s the official CLI in place — with HMR enabled. Your SGLang arguments are forwarded verbatim; the wrapper does not reimplement SGLang's parser.

HMR needs a source tree it can watch. An editable/source SGLang checkout is detected automatically. A regular site-packages install cannot be edited in place, so there it is an error that names the fix rather than copying anything on your behalf:

```bash
sglang-hmr serve facebook/opt-125m --hmr-source-root ~/src/sglang
```

To run without HMR at all:

```bash
sglang-hmr --hmr-disabled serve facebook/opt-125m   # equivalent to `sglang serve facebook/opt-125m`
```

### What the wrapper adds

Unlike vLLM, SGLang has no `--middleware` or `--worker-extension-cls` flags. The wrapper only exports environment variables and prepends a `sitecustomize` directory to `PYTHONPATH`. Every spawned process inherits the environment, so the runtime is injected early in each one.

### HMR options

Each has an environment variable equivalent; CLI options win.

| CLI flag | Environment variable | Purpose |
|----------|----------------------|---------|
| `--hmr-source-root PATH` | `HMR_SGLANG_SOURCE_ROOT` | SGLang source tree to watch; required unless an editable checkout is detected |
| `--hmr-manifest PATH` | `HMR_SGLANG_MANIFEST` | use/verify an existing manifest instead of generating one |
| `--hmr-runtime SPEC` | `HMR_SGLANG_RUNTIME` | replace the packaged runtime with your own `module:callable`; it owns its own scope, so only the source root's existence is checked. The spec is resolved before launch — an unimportable module, a missing attribute, or a non-callable target is a usage error |
| `--hmr-disabled` | `HMR_SGLANG_DISABLED` | plain `sglang` launch: no injection (any non-empty value). An activation already present in the environment is removed, so this also opts out inside a shell or script that exported `HMR_SGLANG_*` earlier |
| `--hmr-print-env` | — | print the computed environment and exec argv, then exit |

## How it works

1. The wrapper resolves the source root, exports `HMR_SGLANG_*`, and prepends its own `sitecustomize` directory to `PYTHONPATH`.
2. It `execve`s the official `sglang`. Because the shim sits on `PYTHONPATH`, CPython imports it during `site` initialization — before SGLang or torch. Any `sitecustomize` it shadows is chained first, so environments that rely on their own keep working.
3. The shim invokes the runtime, which validates the manifest, installs the [pyth-on-line](https://github.com/promplate/pyth-on-line) reactive import hook for the in-scope files only, and starts a watcher.
4. Subprocesses inherit the environment, so scheduler processes get the same early injection. `HMR_SGLANG_SKIP=1` suppresses injection for any process that must not have it.
5. On a file change the watcher publishes immediately after syntax preflight. SGLang has no request-boundary hook, so publication happens from a background thread without an in-flight interlock. The example polls the runtime's own `published` telemetry record before the next real request observes new code.

## Scope

Two files are reloadable, and a manifest must name exactly that set in every one of its fields (`files`, `reactive_paths`, `auto_paths`, `forced_dependents`) — it can neither widen it nor narrow it:

- `python/sglang/srt/model_executor/forward_context.py` — the provider that is actually replaced
- `python/sglang/srt/model_executor/model_runner.py` — its real direct `from … import` consumer, re-executed so the new function object reaches the live request path

A manifest recording the source root, per-file SHA-256, and reactive paths is generated at startup, or verified if you pass `--hmr-manifest`. A missing file, a stale hash, a mismatched source root, a missing or extra path or module, a missing field, a wrongly typed field, unparsable JSON, or an unknown schema version fails immediately, before anything is watched — and a `--hmr-manifest` is verified by the wrapper too. Source files are never modified by this package.

Publication also verifies the source root against reality: an in-scope file the live process did not import from that root is **rejected**, not reported as published. This is what catches a source root that is only a copy of an installed SGLang — the process is running the installed one, so no edit to the copy can reach it.

## Capability limits

The evidence behind this package is one smoke test: SGLang **0.5.16** on the official `lmsysorg/sglang:v0.5.16-xeon` image, CPU device, single scheduler, one request path. See [`examples/sglang-cpu-hmr`](../../examples/sglang-cpu-hmr), which launches through this CLI and asserts that the target function is replaced while listener and scheduler PIDs, the model object, and its parameter pointers stay unchanged.

Verified:

- ✅ One Python function in the request path, replaced live
- ✅ Manifest-listed files with an explicit direct dependent
- ✅ Listener and scheduler process identity, model object, and parameter pointers unchanged
- ✅ Publication from watcher thread without request-boundary interlock

Not verified, not claimed:

- ❌ Any SGLang version other than 0.5.16, and any CUDA/GPU build
- ❌ Multi-rank or multi-node atomic publication (each process publishes independently; there is no cross-rank transaction)
- ❌ Model class, weight, or `nn.Module` hot reload
- ❌ Scheduler, config, entrypoint, or route replacement
- ❌ CUDA kernel, `torch.compile`, or CUDA graph invalidation
- ❌ Request-boundary safety interlock (publication happens from a watcher thread, not at a request boundary)
- ❌ Long-lived production safety

Widening the scope is not a configuration change: it requires new evidence that the target survives in-place replacement.

## License

MIT

## Links

- [Homepage](https://github.com/promplate/hmr)
- [Documentation](https://hmr.promplate.dev)
- [Repository](https://github.com/promplate/hmr/tree/HEAD/packages/sglang-hmr)
- [Working example: sglang-cpu-hmr](../../examples/sglang-cpu-hmr)
