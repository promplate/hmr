# xinference-hmr

Hot Module Replacement for the Xinference process that owns a loaded model.

`xinference-hmr local --host 0.0.0.0 --port 9997` behaves like `xinference-local --host
0.0.0.0 --port 9997`, with one Python source file made reloadable inside the sub-pool
process where the model actually lives. Your Xinference argv is forwarded unchanged and
the official entrypoint is `exec`'d, so the server keeps this process's PID, signal
handling, and exit code. No Xinference flag is injected.

```console
$ xinference-hmr local --host 0.0.0.0 --port 9997
```

## Why this is not a REST-layer reloader

Xinference does not serve inference from the process that terminates HTTP. A request
crosses three processes:

```
xinference-local (REST/uvicorn)  ->  supervisor + worker  ->  sub-pool: ModelActor -> self._model
```

`SupervisorActor.launch_builtin_model` picks a worker; `WorkerActor` spawns a xoscar
sub-pool and calls `xo.create_actor(ModelActor, address=subpool_address, ...)`, then
`await model_ref.load()`. Only that last process holds the weights, the tokenizer, and
the model object. Reloading a module in the REST process changes nothing about what runs
the tokens, so this package installs HMR **only** in the sub-pool, and publishes at
`ModelActor.__on_receive__` — the actor's own request boundary.

Two consequences worth knowing before you read the code:

- **Every process in the tree inherits the injection environment.** xoscar starts a
  sub-pool with `create_subprocess_exec` and an environment copied from the worker, so a
  `PYTHONPATH` shim reaches the REST process, the spawned supervisor/worker, the
  multiprocessing resource tracker, and the sub-pool alike. The environment therefore
  cannot be the gate. `xinference_hmr.runtime.is_model_pool_process` gates on the
  interpreter's own `sys.orig_argv` instead: the sub-pool is the only process exec'd as
  `python -m xoscar.backends.indigen start_sub_pool`. (It renames itself to `Model: <uid>`
  via setproctitle once up, so this check only works from `site` time — which is also the
  only point early enough to matter.)
- **The sub-pool and the REST process can import Xinference from different trees.** The
  sub-pool is started with `-m`, which puts the inherited cwd on `sys.path[0]`. On the
  official CPU image that cwd is `/opt/inference`, the directory pip installed from, while
  the REST process (a console script) imports from site-packages. The two copies are
  byte-identical on a fresh image, so editing the wrong one is invisible until a marker
  never appears. `xinference_hmr.source` resolves the source root from `import xinference`
  and then from the install's `direct_url.json`, and the runtime rejects a publication for
  a module the process did not import from that root.

## Scope

One file is reloadable: `xinference/model/llm/transformers/utils.py`.

It is in scope because of how its consumer calls it. `PytorchModel.batch_inference` does a
function-local `from .utils import batch_inference_one_step` on **every** batch step, so a
re-executed module reaches the live request path through the *existing* model object. No
consumer module has to be re-executed, and no class is redefined — which matters, because
re-executing a module that defines a live class would leave the loaded model an instance of
the old class. That is not HMR, and this package will not report it as such.

Widening the scope is not a configuration change; it needs new evidence for the new target.
A `--hmr-manifest` must name exactly this set, neither wider nor narrower.

## Not implemented

- Model weight, tokenizer, `ModelActor`, and model-class hot replacement.
- Backends other than Transformers: vLLM, SGLang, MLX, and llama.cpp are untested here.
- Virtual-environment sub-pools. Launch with `enable_virtual_env=false` (or
  `XINFERENCE_ENABLE_VIRTUAL_ENV=0`), otherwise the sub-pool runs a different interpreter
  that never sees this shim and HMR is silently absent from the process that owns the model.
- Multi-replica and multi-worker atomic publication. Each sub-pool watches and publishes
  independently; nothing here coordinates a generation switch across replicas.
- Distributed/sharded models across workers, and any GPU path.

## Options

Each is settable as a CLI flag or an environment variable.

| Flag | Variable | Meaning |
| --- | --- | --- |
| `--hmr-source-root PATH` | `HMR_XINFERENCE_SOURCE_ROOT` | Xinference source tree to watch |
| `--hmr-manifest PATH` | `HMR_XINFERENCE_MANIFEST` | JSON manifest pinning the watched set and its hashes |
| `--hmr-runtime SPEC` | `HMR_XINFERENCE_RUNTIME` | override the default runtime entrypoint |
| `--hmr-disabled` | `HMR_XINFERENCE_DISABLED` | plain launch, no injection (any non-empty value) |
| `--hmr-print-env` | — | print the computed environment and exec argv, then exit |

Tuning: `HMR_XINFERENCE_DEBOUNCE_MS` (watcher debounce),
`HMR_XINFERENCE_MAX_WATCHER_RESTARTS` (bounded watcher recovery; `0` makes the first
failure terminal).

## Evidence

`examples/xinference-cpu-hmr` runs the real check on the official `xprobe/xinference:latest-cpu`
image: a tiny Transformers model launched into a real `ModelActor`, a baseline
OpenAI-compatible completion, one `print` inserted into the target function, then the next
completion. It asserts that the marker appears from the sub-pool PID that owns the model,
that the actor object, model object, model class, tokenizer, batch scheduler and parameter
pointers are unchanged, that no second load happened, and that the edited bytes are restored.
