# bentoml-hmr

Request-boundary hot module reload for a modern `@bentoml.service` worker: edit BentoML's own source
and have the next request run it, without restarting the process, re-running the constructor, or
reloading model weights.

This is a research spike, not a general reloader. It republishes exactly one function.

## What it does

`bentoml serve` starts a circus-supervised worker (`python -m _bentoml_impl.worker.service`) that
holds both the HTTP loop and the service instance — the model lives in that worker. `bentoml-hmr`
takes over the worker's command line so HMR is installed *before* any BentoML source is imported,
then publishes source changes on a request boundary.

```sh
bentoml-hmr --manifest <manifest.json> --port 3000 app:MyService
```

The manifest pins the source root and the SHA-256 of every file in scope. A manifest that does not
name this runtime's exact scope, or whose hashes no longer match, is rejected rather than reconciled.

## The seam

`ServiceAppFactory.api_endpoint` re-imports the serde module and re-instantiates the serde on every
request:

```python
from ..serde import ALL_SERDE          # function-local, so it re-resolves per request
serde = ALL_SERDE.get(media_type, ALL_SERDE["application/json"])()
input_data = await method.input_spec.from_http_request(request, serde)
```

That per-request re-resolution is what lets a re-executed module reach live traffic. Everything else
on the path is frozen at startup and stays that way: the route's
`functools.partial(self.api_endpoint_wrapper, name)`, the `ServiceAppFactory`, the `APIMethod`
descriptor, the pydantic input spec, the service instance, and the model weights. The published
function is `JSONSerde.deserialize_model` — real BentoML source on the request path, not example code.

Verified directly: after a reload, a *stale* `JSONSerde` reference keeps running the old
implementation, while a fresh `from ..serde import ALL_SERDE` gets the new one. Module re-execution
is therefore sufficient here; no descriptor rebinding or helper indirection is needed.

## Safety

- **Scope.** Only the body of the published function may change. The candidate's AST is compared
  against the baseline with that one body blanked, so an edit to any other function, import, or
  module-level constant is rejected before the loader sees the file.
- **Boundary.** Publication happens in the worker's own request path, only when no request is in
  flight. An edit that lands mid-request is deferred and applied before the next one.
- **Rollback.** A candidate that fails to parse, fails to execute, or fails its declared contract
  leaves the old implementation live, and stays retryable. The namespace, the loader's dirty flag,
  and the reactive per-name signals are all restored. Rollback restores *bindings*, not in-place
  mutations to shared objects.
- **Single worker.** More than one worker process, or a second BentoML worker watcher, is refused
  rather than silently left un-instrumented.

`--reload` is never used, nothing is restarted, and the service instance and model are never rebuilt.

## Layout

| File | Role |
| --- | --- |
| `bentoml_hmr/__init__.py` | CLI; rewrites the selected circus watcher's command line |
| `bentoml_hmr/runtime.py` | In-worker injection, watcher, request boundary, publication |
| `bentoml_hmr/scope.py` | Manifest build/verify and the AST shape guard |
| `bentoml_hmr/transaction.py` | Namespace / dirty-flag / signal rollback for a failed reload |

See [`examples/bentoml-cpu-hmr`](../../examples/bentoml-cpu-hmr/) for the acceptance harness.
