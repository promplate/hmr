# bentoml-cpu-hmr

A real modern BentoML service — `sshleifer/tiny-gpt2` on CPU behind an OpenAI-compatible
`/v1/chat/completions` — used to prove that [`bentoml-hmr`](../../packages/bentoml-hmr/) republishes
BentoML source into the live worker at a request boundary.

## Setup

The BentoML source under test is a checkout pinned to the commit the harness asserts, installed
editable so the worker imports the same files the harness edits:

```sh
git clone https://github.com/bentoml/BentoML .runtime/BentoML
git -C .runtime/BentoML checkout 517b343b81aeb0b01bbd908e58e53ad9c12ef7eb
uv venv --python 3.12 /path/to/env
uv pip install --python /path/to/env/bin/python \
  'hmr @ git+https://github.com/promplate/pyth-on-line@d410f975367e8a29b17183d108ef09a089e42b63#subdirectory=packages/hmr'
uv pip install --python /path/to/env/bin/python --index-url https://download.pytorch.org/whl/cpu torch
uv pip install --python /path/to/env/bin/python -e .runtime/BentoML -e ../../packages/bentoml-hmr openai httpx transformers
```

HMR comes from that commit, never from PyPI: the PyPI wheel and the commit both call themselves
`0.7.6.2` but differ in `reactivity/`, so a version string cannot tell them apart. `--hmr-checkout`
points at a plain clone of the same commit, outside the venv, from whose git objects the harness
computes the hashes it expects — hashing the installed file twice would only prove it equals itself.

```sh
git clone https://github.com/promplate/pyth-on-line /path/to/pyth-on-line
git -C /path/to/pyth-on-line checkout d410f975367e8a29b17183d108ef09a089e42b63
```

`.runtime/` and `results/` are ignored.

## Running

```sh
python smoke.py --source "$PWD/.runtime/BentoML/src" --hmr-checkout /path/to/pyth-on-line --results "$PWD/results/preflight-02" --preflight
python smoke.py --source "$PWD/.runtime/BentoML/src" --hmr-checkout /path/to/pyth-on-line --results "$PWD/results/full-03"
```

The preflight runs plain `bentoml serve` and checks only that the service is real: one 200 from a
genuine generation, one weight load, stable identities, exact source restore. The full run adds HMR.

Each run writes `receipt.json`, `full.log`, and the `manifest.json` it handed the runtime.

## The API

`chat_completions` takes its request model **positionally** (`payload: ChatCompletionRequest, /`),
which makes the HTTP body the OpenAI payload itself instead of nesting it under a parameter name.
The official `openai` client parses the response into a `ChatCompletion` — verified against a live
server, not just asserted over raw JSON:

```
parsed type: ChatCompletion
content 'Hello there stairs stairs stairs stairs'
usage 2 4 6
```

The output is real generation, not an echo: the harness requires the completion to extend the prompt
and to report exactly the requested number of completion tokens.

## What the full run proves

The mutation inserts one unique `print` into `JSONSerde.deserialize_model` — BentoML's own source, on
the path of every JSON request.

| Assertion | Meaning |
| --- | --- |
| `hmr_pinned_to_commit` | The installed HMR carries `direct_url.json` naming the pinned commit, and all 21 `reactivity/*.py` match that commit's git objects |
| `baseline_200_real_generation` | 200 with real generated tokens before any edit |
| `worker_only_injection` | Every HMR event comes from the one worker PID that loaded the weights |
| `inflight_deferred` | An edit landing mid-request is deferred; no marker leaks into the held request |
| `worker_publication_and_marker` | Published in the model-holding worker with no request active, function identity changed, marker printed with that worker's PID |
| `syntax_rollback_retry` / `runtime_rollback_retry` | A broken candidate is rejected, the old implementation keeps serving, and it stays retryable |
| `syntax_recovers` / `runtime_recovers` | A fixed candidate publishes afterwards |
| `one_changed_source` | Exactly one file on disk differs from the pre-run hashes |
| `restored_implementation` | After restoring the original bytes, the marker stops appearing |
| `identity_continuous` | Same PID, model object, model class, service instance, and weight `data_ptr` across every request |
| `single_setup_and_weight_load` | Constructor and weight load each happened exactly once |
| `process_group_gone`, `port_closed`, `source_bytes_restored` | Cleanup left no process, no listener, and no source drift |

Observed on commit `517b343b`, bentoml `1.4.39.post8+g517b343b`, hmr from `d410f975` with
`reactivity/hmr/core.py` at `e89f00a3aaf9ad9451e3fd4e63680d40783a08452f142f494e23f962d0e544eb`,
torch `2.14.0+cpu`, transformers `5.17.0`: 16/16 assertions, 10/10 responses 200, 4 publications and
4 rejections, one weight load, one identity tuple throughout.

No `--reload`, no restart, no second constructor call, no weight reload.

## Scope

One service, one worker, one published function. Multiple workers, multiple services, and streaming
or task endpoints are out of scope for this spike.
