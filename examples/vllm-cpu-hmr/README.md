# Real vLLM CPU HMR example

This example demonstrates a narrow, disposable HMR boundary in a real vLLM
OpenAI-compatible server. It is not a claim that arbitrary vLLM modules,
compiled kernels, CUDA Graphs, or model weights are safe to replace in place.

## Run

From the repository root:

```bash
docker pull vllm/vllm-openai-cpu:v0.28.0-x86_64
./examples/vllm-cpu-hmr/run.sh
```

The script builds a local image from the official vLLM CPU image, fetches and
installs `hmr` from the pinned `promplate/pyth-on-line` commit
`d410f975367e8a29b17183d108ef09a089e42b63`, starts one real vLLM server, and
writes:

```text
vllm-cpu-hmr-results/cpu-smoke-receipt.json
vllm-cpu-hmr-results/cpu-smoke-full.log
```

Set `VLLM_HMR_MODEL` to use another compatible small model and
`VLLM_HMR_RESULTS` to choose another output directory. The default model is
`facebook/opt-125m` so the smoke is practical on a CPU-only machine.

## What the smoke proves

The runner performs exactly this sequence:

1. Start the official `vllm/vllm-openai-cpu:v0.28.0-x86_64` image.
2. Complete one real `POST /v1/completions` request.
3. Insert one unique `print` into the existing vLLM function
   `vllm/renderers/inputs/preprocess.py:extract_prompt_components`.
4. Wait for the manifest watcher to observe that file.
5. Publish the candidate at the next request boundary. The direct
   `from vllm.renderers.inputs.preprocess import extract_prompt_components`
   consumer is explicitly invalidated and re-executed.
6. Complete the next real OpenAI API request without restarting the server.
7. Require the marker in the API process log, unchanged API/worker PIDs,
   unchanged model object/class/parameter pointers, no model-load evidence,
   and HTTP 200.
8. Restore the edited file byte-for-byte and remove the container.

The manifest and receipt make the source layout explicit. The official CPU
image installs vLLM under `site-packages`; the Dockerfile copies that package
byte-for-byte to an external source root because pyth-on-line intentionally
excludes virtualenv/site-packages paths from reactive wrapping. The HMR core is
not assumed to exist under `promplate/hmr/packages/hmr`: that path is absent
in the current cookbook checkout, so the build fetches the pinned core source
from `promplate/pyth-on-line` explicitly.

The HMR watcher is injected by `sitecustomize.py` before vLLM imports. The
short-lived vLLM model-registry inspection helper is excluded from watcher
installation because it runs during architecture inspection and must not
inherit a native watch thread. The API and model worker processes still prove
that the HMR injection and manifest are active.

## Scope and limits

This is an example and validation boundary, not a production deployment
recipe. It covers one CPU/Python request path with one worker. It does not
prove safety for:

- arbitrary vLLM source files;
- scheduler or lifecycle modules;
- model class replacement;
- weight reloads;
- Triton/CUDA kernels;
- `torch.compile` or CUDA Graphs;
- multi-rank atomic publication;
- long-lived production processes.

Keep the lock in `run.sh` when running this alongside another engine's CPU
experiment. The generated results are intentionally not committed.
