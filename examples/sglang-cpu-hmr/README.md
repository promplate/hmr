# Real SGLang CPU HMR example

This example demonstrates a narrow, disposable HMR boundary in a real SGLang
server. It is not a claim that arbitrary SGLang modules, compiled kernels, CUDA
Graphs, or model weights are safe to replace in place.

## Run

From the repository root:

```bash
docker pull lmsysorg/sglang:v0.5.16-xeon
./examples/sglang-cpu-hmr/run.sh
```

The script builds a local image from the official SGLang CPU image, fetches and
installs `hmr` from the pinned `promplate/pyth-on-line` commit
`d410f975367e8a29b17183d108ef09a089e42b63`, starts one real SGLang server, and
writes:

```text
sglang-cpu-hmr-results/cpu-smoke-receipt.json
sglang-cpu-hmr-results/cpu-smoke-full.log
```

Set `SGLANG_HMR_MODEL` to use another compatible small model and
`SGLANG_HMR_RESULTS` to choose another output directory. The default model is
`facebook/opt-125m` so the smoke is practical on a CPU-only machine.

## What the smoke proves

The runner performs exactly this sequence:

1. Start the official `lmsysorg/sglang:v0.5.16-xeon` image and launch the
   server through the `sglang-hmr` console script built from this checkout.
2. Complete one real `POST /generate` request.
3. Insert one unique `print` into the existing SGLang function
   `python/sglang/srt/model_executor/forward_context.py:has_forward_context`.
4. Wait for the watcher to publish the change.
5. Complete the next real `/generate` request without restarting the server.
6. Require the marker in the scheduler process log, unchanged listener/scheduler PIDs,
   the official SGLang argv reaching `sglang` untouched,
   unchanged model object/class/parameter pointers, no model-load evidence,
   and HTTP 200.
7. Restore the edited file byte-for-byte and remove the container.

The manifest and receipt make the source layout explicit. The official CPU
image installs SGLang under `site-packages`; the Dockerfile copies that package
byte-for-byte to an external source root because pyth-on-line intentionally
excludes virtualenv/site-packages paths from reactive wrapping. The HMR core is
fetched from the pinned `promplate/pyth-on-line` commit explicitly.

## How the server is launched

The smoke never runs `sglang` itself. It runs the installed
[`sglang-hmr`](../../packages/sglang-hmr) console script:

```text
sglang-hmr --hmr-source-root /opt/sglang-release-source \
           --hmr-manifest /results/cpu-source-manifest.json \
           serve facebook/opt-125m --host 127.0.0.1 --port 31000 \
           --dtype float32 --max-total-tokens 256 \
           --mem-fraction-static 0.7 --device cpu
```

No `--hmr-runtime`: the runtime under test is the packaged default. The wrapper
strips every `--hmr-*` option into `HMR_SGLANG_*`, prepends its own
`sitecustomize` shim to `PYTHONPATH`, and `exec`s the official SGLang CLI in
place, so the server keeps the launcher's PID.

The HMR watcher is therefore injected before SGLang imports, in every process
that inherits the environment. The listener and scheduler processes prove that
the HMR injection and manifest are active.

## Scope and limits

This is an example and validation boundary, not a production deployment
recipe. It covers one CPU request path with one scheduler. It does not
prove safety for:

- arbitrary SGLang source files;
- scheduler or lifecycle modules;
- model class replacement;
- weight reloads;
- CUDA kernels;
- `torch.compile` or CUDA Graphs;
- multi-rank atomic publication;
- request-boundary safety interlock;
- long-lived production processes.

Keep the lock in `run.sh` when running this alongside another engine's CPU
experiment. The generated results are intentionally not committed.
