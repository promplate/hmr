# Xinference CPU HMR example

Real-model CPU smoke for `xinference-hmr` on the official `xprobe/xinference:latest-cpu`
image. It launches a tiny Transformers model into a real `ModelActor`, makes a real
OpenAI-compatible request, inserts one `print` into one Xinference source function, makes
the next request, and asserts that the new code ran **inside the sub-pool process that owns
the loaded model** without reloading, relaunching, or rebuilding anything.

```console
$ export XINFERENCE_HMR_MODEL_DIR=/path/to/a/local/hf/model
$ bash examples/xinference-cpu-hmr/run.sh                      # /v1/completions
$ XINFERENCE_HMR_MODE=chat bash examples/xinference-cpu-hmr/run.sh   # /v1/chat/completions
```

Both write a receipt, a full log, the actor-process identity records, and the source
manifest into `$XINFERENCE_HMR_RESULTS` (default
`examples/xinference-cpu-hmr/xinference-cpu-hmr-results`).

## One model, one endpoint per run

`--mode completion` launches the builtin `gpt-2` family; `--mode chat` registers a
chat-only custom family over the same local weights. They are separate runs because a
family declaring `chat` instantiates `PytorchChatModel`, whose `prepare_batch_inference`
puts *every* request through `_get_full_prompt` — so a raw-string `/v1/completions` prompt
reaches `convert_messages_with_content_list_to_str_conversion` and raises `'str' object has
no attribute 'get'`. That is Xinference's own behaviour for chat models, not an HMR
failure. `batch_inference_one_step` is the same target on both paths, so each mode is a
complete proof on its own, and keeping them separate keeps every identity assertion about a
single actor.

## What the smoke actually proves

Each run asserts 23 things. The ones that carry the claim:

- The server was started through the `xinference-hmr` console script, which `exec`'d the
  official `xinference-local` with the `--hmr-*` options stripped (read back from the
  launcher PID's own `/proc/<pid>/cmdline`).
- A real model reached a real `ModelActor`: `/v1/models` lists it, `ModelActor.load` ran in
  a sub-pool process, and the baseline request returned 200 with generated text.
- HMR is installed **in that sub-pool**, not in the REST process, and it loaded the exact
  source manifest the smoke wrote.
- The actor's live module was imported from the watched source root — see below.
- Exactly one Xinference `.py` changed, containing exactly one unique marker `print`.
- The marker was absent until the next real request, then appeared **from the sub-pool PID
  that owns the model**, and the live function object's `co_consts` carries the new marker.
- The runtime's own telemetry shows the watcher queueing the file *before* a publication,
  and the publication happening at an `actor_boundary` — the `ModelActor.__on_receive__`
  wrapper — not on a watcher thread's own schedule.
- Across the edit: same PID, same actor object and class, same model object and class, same
  tokenizer, same batch scheduler, identical parameter storage pointers, exactly one
  `load_exit` record, and no weight-load lines in the post-edit log.
- The edited bytes were restored, no other source differs, and the process group is gone.

A negative control was run separately (not part of `run.sh`): the same edit, the same two
real 200 requests, launched with `--hmr-disabled`. The marker appeared **0** times and no
process environment carried an `HMR_XINFERENCE_*` variable — so the marker in the passing
runs comes from `xinference_hmr` publishing, not from anything Python does on its own.

## The trap this example exists to catch

The model sub-pool and the REST process can import Xinference from **different trees**.
xoscar starts a sub-pool as `python -m xoscar.backends.indigen ...` with the worker's cwd
inherited, and `-m` puts that cwd on `sys.path[0]`. The official image installs from
`/opt/inference`, so:

- launched from `/opt/inference`, the sub-pool imports the source tree (watchable);
- launched from anywhere else, it falls through to site-packages, while the watcher still
  happily watches `/opt/inference`.

In the second case the watcher sees every edit and publishes none of them, and the only
symptom is a marker that never arrives. Both copies are byte-identical on a fresh image, so
nothing about a running server reveals which one it is using. This was hit for real during
development: the smoke's first run failed on exactly this assertion.

Two things guard it now. `smoke.py` passes `cwd=source` when launching, and asserts the
actor's live `module_file` equals the watched target. And `xinference_hmr.runtime`
re-resolves the root against `xinference.__file__` *inside the sub-pool* and refuses to
install when they disagree.

## Observability is setup, not the mutation

The identity records come from `hmr_xinference_probe/sitecustomize.py`, reached through the
wrapper's documented `sitecustomize` chaining. It wraps `ModelActor.load/generate/chat` to
record identity and reads `xinference_hmr`'s state from inside the actor process. It
installs no watcher, publishes nothing, and holds no HMR state — every HMR decision in a
run is made by the package under test. No Xinference source file is edited to read identity
back; the single post-baseline mutation is the `print` in `batch_inference_one_step`.

## Requirements

- `xprobe/xinference:latest-cpu` pulled locally.
- A local HF model directory in `XINFERENCE_HMR_MODEL_DIR`. Anything tiny and
  Transformers-loadable works; the runs on record used `hf-internal-testing/tiny-random-gpt2`
  (5 layers, 32 hidden, ~450 KB), copied out of the HF cache so the container needs no network.
- The run takes the `/tmp/hmr-engine-cpu.lock` flock, so concurrent CPU engine smokes do not
  interfere.
