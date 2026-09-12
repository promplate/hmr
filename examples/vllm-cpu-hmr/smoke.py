#!/usr/bin/env python3
"""One-item real-vLLM CPU HMR smoke for the official CPU image."""

# pyright: reportReturnType=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportArgumentType=false, reportAttributeAccessIssue=false

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mutations import MutationSet, print_statement

TARGET = "vllm/renderers/inputs/preprocess.py"
FUNCTION = "extract_prompt_components"
DEPENDENT = "vllm.v1.engine.async_llm"
DEPENDENT_PATH = "vllm/v1/engine/async_llm.py"
DEFAULT_RUNTIME = "vllm_hmr.runtime.bootstrap:install_unless_registry_inspector"
PACKAGE_MIDDLEWARE = "vllm_hmr.runtime.middleware.HMRBoundaryMiddleware"
PROBE_WORKER_EXTENSION = "hmr_vllm_probe.worker.HMRProbeWorkerExtension"
MARKER = "HMR_PROBE_VLLM_CPU_PRINT_D410F975_001"
WORKER_OUT_OF_MAP = "not loaded from this source root"  # `vllm_hmr.runtime.bootstrap`'s refusal when a worker never imported the target
PYTH_SHA = "d410f975367e8a29b17183d108ef09a089e42b63"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def python_source_hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha256(path) for path in sorted((root / "vllm").rglob("*.py"))}


def request_json(
    base: str,
    method: str,
    path: str,
    body: dict | None = None,
    timeout: float = 300,
):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return (
                response.status,
                {key.lower(): value for key, value in response.headers.items()},
                json.loads(raw) if raw else None,
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = raw.decode(errors="replace")
        return (
            exc.code,
            {key.lower(): value for key, value in exc.headers.items()},
            payload,
        )


def completion(base: str, model: str):
    return request_json(
        base,
        "POST",
        "/v1/completions",
        {
            "model": model,
            "prompt": "The next integer after 40 is",
            "max_tokens": 4,
            "temperature": 0,
            "seed": 7,
        },
    )


def state(base: str) -> dict[str, Any]:
    status, _, payload = request_json(base, "GET", "/__hmr__/state")
    if status != 200:
        raise AssertionError(payload)
    return payload


def wait_ready(base: str, process: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"vLLM exited during startup with {process.returncode}")
        try:
            status, _, payload = request_json(base, "GET", "/health", timeout=2)
            if status == 200:
                return
            last = payload
        except (OSError, TimeoutError) as exc:
            last = repr(exc)
        time.sleep(1)
    raise TimeoutError(f"vLLM did not become ready: {last}")


def identity(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "api_pid": snapshot["api"]["telemetry"]["pid"],
        "workers": [
            {
                "pid": worker["pid"],
                "model_id": worker["model_id"],
                "model_class_id": worker["model_class_id"],
                "model_class": worker["model_class"],
                "parameter_sample": worker["parameter_sample"],
            }
            for worker in snapshot["workers"]
        ],
    }


def load_lines(text: str) -> list[str]:
    needles = (
        "starting to load model",
        "loading model weights",
        "loading weights",
        "model loading took",
    )
    return [line for line in text.splitlines() if any(needle in line.lower() for needle in needles)]


def publication_for_target(snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The request boundary that published the target, and the published record itself."""
    for event in reversed(snapshot["api"]["telemetry"]["events"]):
        local_sync = event.get("local_sync")
        if event.get("kind") != "request_boundary" or not isinstance(local_sync, dict):
            continue
        for published in local_sync.get("published", []):
            if published.get("path") == TARGET:
                return event, published
    return None


def queued_before(snapshot: dict[str, Any], boundary: dict[str, Any]) -> dict[str, Any] | None:
    """The watcher's own `source_change` for the target, required to precede that boundary."""
    for event in snapshot["api"]["telemetry"]["events"]:
        if event.get("kind") == "source_change" and event.get("path") == TARGET and event["t"] < boundary["t"]:
            return event
    return None


def worker_queued_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Each worker runs its own watcher; seeing the same edit is what proves the runtime is live there."""
    return [
        {"pid": worker["pid"], "rank": worker["rank"], **event}
        for worker in snapshot["workers"]
        for event in worker["hmr"]["telemetry"]["events"]
        if event.get("kind") == "source_change" and event.get("path") == TARGET
    ]


def wait_worker_queued(base: str, expected: int, timeout: float = 120) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        queued = worker_queued_events(state(base))
        if len({item["pid"] for item in queued}) >= expected:
            return queued
        last = queued
        time.sleep(0.2)
    raise TimeoutError(f"not every worker's watcher queued {TARGET}: {last}")


def worker_boundary_decisions(snapshot: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The inference boundary that asked the workers to publish, and what each of them answered."""
    for event in reversed(snapshot["api"]["telemetry"]["events"]):
        if event.get("kind") == "request_boundary" and event.get("path") == "/v1/completions" and event.get("worker_sync"):
            return event, list(event["worker_sync"])
    raise AssertionError("no inference boundary asked the workers to publish")


def wait_published(base: str, timeout: float = 120) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:  # (boundary, published, queued)
    """Poll the state endpoint until the packaged runtime publishes the target.

    For the packaged middleware every HTTP request is a publication boundary, so this
    poll is itself what triggers the publication it waits for; there is no interval to
    guess. Ordering is not inferred from wall clock either: the returned snapshot carries
    the watcher's own `source_change` and the boundary that consumed it.
    """
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        snapshot = state(base)
        found = publication_for_target(snapshot)
        if found is not None:
            boundary, published = found
            queued = queued_before(snapshot, boundary)
            if queued is None:
                raise AssertionError(f"the target was published with no preceding watcher event: {boundary}")
            return boundary, published, queued
        last = snapshot["api"]["pending"]
        time.sleep(0.2)
    raise TimeoutError(f"the packaged runtime did not publish {TARGET}: pending={last}")


def run(args: argparse.Namespace) -> None:
    source = Path(args.source).resolve()
    results = Path(args.results).resolve()
    results.mkdir(parents=True, exist_ok=True)
    log_path = results / "cpu-smoke-full.log"
    receipt_path = results / "cpu-smoke-receipt.json"

    target = source / TARGET
    if not target.is_file():
        raise FileNotFoundError(f"release/source mismatch: runtime target absent: {target}")
    original_target_hash = sha256(target)
    baseline_hashes = python_source_hashes(source)
    # Exactly what `vllm_hmr.runtime.scope.build_manifest` would produce for this root:
    # written out and passed back with --hmr-manifest so the receipt records the bytes
    # the runtime verified, rather than a probe-shaped manifest the product would reject.
    manifest = {
        "schema_version": 1,
        "source_root": str(source),
        "files": [{"path": path, "sha256": sha256(source / path)} for path in (TARGET, DEPENDENT_PATH)],
        "reactive_paths": [TARGET, DEPENDENT_PATH],
        "auto_paths": [TARGET],
        "forced_dependents": {TARGET: [DEPENDENT]},
    }
    manifest_path = results / "cpu-source-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    env = os.environ.copy()
    # `vllm-hmr` sets HMR_VLLM_ENABLE, _RUNTIME, _SOURCE_ROOT and _MANIFEST itself; the
    # packaged runtime reads no other knob. Only vLLM's own variables are set here.
    env.update(
        {
            "VLLM_PLUGINS": "hmr_vllm_probe",
            "VLLM_CPU_KVCACHE_SPACE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTH_ON_LINE_SHA": PYTH_SHA,
        }
    )
    # No --hmr-runtime: this must exercise the packaged default runtime.
    hmr_argv = ["--hmr-source-root", str(source), "--hmr-manifest", str(manifest_path)]
    vllm_argv = [
        "serve",
        args.model,
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
        "--dtype",
        "float32",
        "--max-model-len",
        "64",
        "--max-num-seqs",
        "1",
        "--enforce-eager",
        "--distributed-executor-backend",
        "uni",
        # A subclass of the packaged extension: it inherits the publication RPC the
        # packaged middleware calls and adds one read-only model identity RPC. No
        # --middleware here, so the CLI must inject the packaged one itself.
        "--worker-extension-cls",
        PROBE_WORKER_EXTENSION,
    ]
    # What the official `vllm` must receive: the argv above plus the CLI's own injection.
    expected_vllm_argv = [*vllm_argv, "--middleware", PACKAGE_MIDDLEWARE]
    launcher = shutil.which("vllm-hmr")
    if launcher is None:
        raise FileNotFoundError("the `vllm-hmr` console script is not installed on PATH")
    command = [launcher, *hmr_argv, *vllm_argv]
    process: subprocess.Popen | None = None
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "status": "failed",
        "started_at": time.time(),
        "official_image": args.image,
        "official_image_id": args.image_id,
        "official_image_digest": args.image_digest,
        "vllm_distribution_version": args.vllm_version,
        "installed_distribution_source": args.installed_source,
        "runtime_source_root": str(source),
        "runtime_source_resolution": (
            "The official v0.28.0+cpu wheel lives under site-packages, which "
            "pyth-on-line d410f975 excludes by design. The container copies that "
            "installed vllm package byte-for-byte to an external runtime source "
            "root and imports the copy first; the smoke does not assume that "
            "vLLM source is present in this repository."
        ),
        "reactive_scope": (
            "Only the manifest-listed provider and its actual direct from-import consumer are reactive. This proves this one request-path Python function/dependency chain, not arbitrary vLLM modules."
        ),
        "pyth_on_line_sha": PYTH_SHA,
        "pyth_core_path": args.pyth_core_path,
        "pyth_core_sha256": args.pyth_core_sha256,
        "target": {
            "path": TARGET,
            "function": FUNCTION,
            "original_sha256": original_target_hash,
        },
        "marker": MARKER,
        "model": args.model,
        "command": command,
        "launcher": {
            "console_script": launcher,
            "hmr_only_argv": hmr_argv,
            "official_vllm_argv": vllm_argv,
            "expected_vllm_argv_after_cli_injection": expected_vllm_argv,
            "note": "the smoke never runs `vllm` directly: `vllm-hmr` strips every --hmr-* option into HMR_VLLM_* and execs the official CLI in place",
        },
        "runtime_under_test": {
            "runtime": DEFAULT_RUNTIME,
            "runtime_source": "CLI default: --hmr-runtime is not passed",
            "middleware": PACKAGE_MIDDLEWARE,
            "middleware_source": "CLI default: --middleware is not passed, the wrapper appends the packaged one",
            "worker_extension": PROBE_WORKER_EXTENSION,
            "worker_extension_source": (
                "example subclass of vllm_hmr.runtime.worker.HMRWorkerExtension. It overrides nothing and inherits the "
                "publication RPC the packaged middleware calls; it adds one read-only RPC returning id(model), the model "
                "class and parameter data pointers, which the packaged extension deliberately does not report."
            ),
            "probe_scope": (
                "hmr_vllm_probe is an observability-only vLLM endpoint plugin plus that worker subclass. It installs no "
                "watcher, publishes nothing, and holds no HMR state: every HMR decision in this run is made by vllm_hmr."
            ),
        },
        "manifest_path": str(manifest_path),
        "log_path": str(log_path),
        "receipt_path": str(receipt_path),
        "assertions": {},
    }
    try:
        with log_path.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                command,
                cwd=source,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        base = f"http://127.0.0.1:{args.port}"
        wait_ready(base, process, args.startup_timeout)

        baseline_status, baseline_headers, baseline_payload = completion(base, args.model)
        if baseline_status != 200:
            raise AssertionError((baseline_status, baseline_payload))
        before = state(base)
        before_identity = identity(before)
        if not before_identity["workers"]:
            raise AssertionError("worker identity endpoint returned no workers")
        if not before["api"]["installed"]:
            raise AssertionError("early pyth-on-line injection is not installed in API process")
        if before["api"].get("manifest") != manifest:
            raise AssertionError(f"packaged runtime did not load the exact source manifest: {before['api'].get('manifest')}")
        if any(not worker["hmr"]["installed"] or worker["hmr"].get("manifest") != manifest for worker in before["workers"]):
            raise AssertionError("early injection/manifest was not active in every model worker")
        # `installed: True` here is reported by `vllm_hmr.runtime.bootstrap` itself: the probe
        # owns no bootstrap, so only the packaged default runtime can have produced this state.
        if not all(worker["hmr"]["manifest"]["forced_dependents"] == {TARGET: [DEPENDENT]} for worker in before["workers"]):
            raise AssertionError("workers did not load the packaged forced-dependent mapping")
        # The API process reports the argv its interpreter was actually started with.
        # `vllm-hmr` exec'd into this PID, so this is proof of both the strip and the handover.
        api_orig_argv = before["api"]["telemetry"]["orig_argv"]
        if not api_orig_argv[1].endswith("/vllm"):
            raise AssertionError(f"API process was not exec'd into the official vllm console script: {api_orig_argv}")
        # Equality against `expected_vllm_argv` is the proof that the CLI appended the packaged
        # middleware itself: the smoke never passed --middleware.
        if api_orig_argv[2:] != expected_vllm_argv:
            raise AssertionError(f"forwarded argv differs from the official vLLM argv: {api_orig_argv[2:]} != {expected_vllm_argv}")
        if any(token.startswith("--hmr-") for token in api_orig_argv):
            raise AssertionError(f"HMR-only options leaked into the official vllm argv: {api_orig_argv}")
        before_log = log_path.read_text(encoding="utf-8", errors="replace")
        load_evidence_before = load_lines(before_log)

        with MutationSet(source) as edits:
            inserted_line = edits.insert_statement(TARGET, FUNCTION, print_statement(MARKER))
            mutated_hash = sha256(target)
            if mutated_hash == original_target_hash:
                raise AssertionError("target bytes did not change")
            changed_while_mutated = sorted(key for key, digest in python_source_hashes(source).items() if baseline_hashes.get(key) != digest)
            if changed_while_mutated != [TARGET]:
                raise AssertionError(f"expected exactly one changed Python source, got {changed_while_mutated}")
            mutated_text = target.read_text(encoding="utf-8")
            statement = print_statement(MARKER)
            if mutated_text.count(MARKER) != 1 or mutated_text.count(statement) != 1:
                raise AssertionError("mutation did not insert exactly one unique print")

            publication_boundary, publication, queued_event = wait_published(base)
            if DEPENDENT not in publication.get("forced_dependents_reexecuted", []):
                raise AssertionError(f"direct from-import dependent was not reexecuted: {publication}")
            # Waited for before the next inference so that the boundary below finds something to
            # decide in every worker, instead of racing their watchers and reporting an empty sync.
            worker_queued = wait_worker_queued(base, len(before_identity["workers"]))
            # Only now is the new code in place, so this is the first request that can run it.
            post_status, post_headers, post_payload = completion(base, args.model)
            if post_status != 200:
                raise AssertionError((post_status, post_payload))
            after = state(base)
            after_identity = identity(after)
            if after_identity != before_identity:
                raise AssertionError((before_identity, after_identity))
            # A `/v1/` path is the only boundary at which the packaged middleware asks the workers
            # to publish, so this request is where each of them answered. `TARGET` is imported by
            # the API process alone (`async_llm.py` is a frontend module), so a worker refusing to
            # call it published is the runtime declining a swap it never made; a worker reporting
            # a publication for a module absent from its own map would be the bug.
            worker_boundary, worker_decisions = worker_boundary_decisions(after)
            if len(worker_decisions) != len(after_identity["workers"]):
                raise AssertionError(f"the boundary did not reach every worker: {worker_decisions}")
            for decision in worker_decisions:
                if not decision.get("installed") or decision.get("deferred"):
                    raise AssertionError(f"a worker did not run the packaged publication RPC: {decision}")
                decided = [*decision.get("published", []), *decision.get("rejected", [])]
                if [record["path"] for record in decided] != [TARGET]:
                    raise AssertionError(f"a worker decided something other than exactly {TARGET}: {decision}")
                error = next((item["error"] for item in decision.get("rejected", ()) if item["path"] == TARGET), None)
                if error is not None and WORKER_OUT_OF_MAP not in error:
                    raise AssertionError(f"a worker rejected the target for an unexpected reason: {decision}")
            log_after = log_path.read_text(encoding="utf-8", errors="replace")
            marker_line = f"{MARKER} pid={before_identity['api_pid']}"
            if marker_line not in log_after:
                raise AssertionError(f"marker missing from correct process log: {marker_line}")
            post_edit_log = log_after[len(before_log) :]
            load_evidence_after_edit = load_lines(post_edit_log)
            if load_evidence_after_edit:
                raise AssertionError(f"model reload evidence appeared after edit: {load_evidence_after_edit}")

            receipt.update(
                {
                    "status": "passed",
                    "baseline": {
                        "http_status": baseline_status,
                        "response_id": baseline_payload.get("id"),
                        "choice_text": baseline_payload["choices"][0]["text"],
                        "headers": baseline_headers,
                    },
                    "post_edit": {
                        "http_status": post_status,
                        "response_id": post_payload.get("id"),
                        "choice_text": post_payload["choices"][0]["text"],
                        "headers": post_headers,
                    },
                    "identity_before": before_identity,
                    "identity_after": after_identity,
                    "inserted_line": inserted_line,
                    "mutated_sha256": mutated_hash,
                    "changed_python_sources_while_mutated": changed_while_mutated,
                    "watcher_queued_event": queued_event,
                    "publication": publication,
                    "publication_boundary_path": publication_boundary.get("path"),
                    "worker_queued_events": worker_queued,
                    "worker_boundary_path": worker_boundary.get("path"),
                    "worker_boundary_decisions": worker_decisions,
                    "load_evidence_before_edit": load_evidence_before,
                    "load_evidence_after_edit": load_evidence_after_edit,
                    "marker_log_line": marker_line,
                    "api_process_orig_argv": api_orig_argv,
                    "assertions": {
                        "service_started_through_vllm_hmr_console_script": True,
                        "hmr_options_stripped_before_official_vllm_exec": True,
                        "packaged_default_runtime_not_overridden": "--hmr-runtime" not in command,
                        "packaged_middleware_appended_by_cli": True,
                        "packaged_worker_extension_inherited_by_probe_subclass": True,
                        "baseline_real_openai_inference_200": True,
                        "early_injection_active_in_api_and_model_worker": True,
                        "exactly_one_vllm_python_source_changed": True,
                        "exactly_one_unique_print_inserted": True,
                        "target_is_not_entrypoint": "entrypoint" not in TARGET,
                        "watcher_queued_target_before_the_publishing_boundary": True,
                        "reactive_scope_exact_provider_and_direct_importer_only": True,
                        "direct_importer_explicitly_invalidated_and_reexecuted": True,
                        "every_worker_watcher_queued_the_target_in_its_own_process": True,
                        "every_worker_answered_the_boundary_publication_rpc": True,
                        "no_worker_claimed_a_publication_outside_its_own_module_map": True,
                        "next_real_openai_inference_200": True,
                        "marker_in_api_process_log": True,
                        "edited_function_traversed_by_unique_print": True,
                        "same_api_and_worker_pids": True,
                        "same_model_object_class_and_parameter_pointers": True,
                        "no_model_reload_log_after_edit": True,
                        "server_process_not_restarted": process.poll() is None,
                    },
                }
            )
        restored_hash = sha256(target)
        restored_changes = sorted(key for key, digest in python_source_hashes(source).items() if baseline_hashes.get(key) != digest)
        receipt["target"]["restored_sha256"] = restored_hash
        receipt["changed_python_sources_after_restore"] = restored_changes
        receipt["assertions"]["edited_bytes_restored"] = restored_hash == original_target_hash and not restored_changes
        if not receipt["assertions"]["edited_bytes_restored"]:
            receipt["status"] = "failed"
            raise AssertionError((original_target_hash, restored_hash, restored_changes))
    except BaseException as exc:
        receipt["status"] = "failed"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        # Teardown happens in `finally`, so the server is still up here: a failed receipt that
        # carries no runtime state is worth almost nothing when the container is already gone.
        if process is not None and process.poll() is None:
            try:
                receipt["state_at_failure"] = state(f"http://127.0.0.1:{args.port}")
            except Exception as probe_error:
                receipt["state_at_failure_error"] = f"{type(probe_error).__name__}: {probe_error}"
        raise
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=30)
        receipt["server_exit_code_after_teardown"] = None if process is None else process.returncode
        receipt["server_stopped"] = process is None or process.poll() is not None
        receipt["source_restored"] = sha256(target) == original_target_hash
        receipt["finished_at"] = time.time()
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "status": receipt["status"],
                "receipt_path": str(receipt_path),
                "log_path": str(log_path),
            }
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--model", default="facebook/opt-125m")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--startup-timeout", type=float, default=900)
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--vllm-version", required=True)
    parser.add_argument("--installed-source", required=True)
    parser.add_argument("--pyth-core-path", required=True)
    parser.add_argument("--pyth-core-sha256", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
