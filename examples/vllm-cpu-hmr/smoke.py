#!/usr/bin/env python3
"""One-item real-vLLM CPU HMR smoke for the official CPU image."""

# pyright: reportReturnType=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportArgumentType=false, reportAttributeAccessIssue=false

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
MARKER = "HMR_PROBE_VLLM_CPU_PRINT_D410F975_001"
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


def wait_manifest_pending(status_dir: Path, api_pid: int, timeout: float = 30) -> dict[str, Any]:
    path = status_dir / f"{api_pid}.json"
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            last = current
            if any(item.get("path") == TARGET for item in current.get("pending", [])):
                return current
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            last = repr(exc)
        time.sleep(0.05)
    raise TimeoutError(f"manifest watcher did not queue {TARGET}: {last}")


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


def target_counter(snapshot: dict[str, Any], function: str) -> int:
    counters = snapshot["api"]["telemetry"]["counters"]
    return sum(value for key, value in counters.items() if key.endswith(f"preprocess.py:{function}"))


def load_lines(text: str) -> list[str]:
    needles = (
        "starting to load model",
        "loading model weights",
        "loading weights",
        "model loading took",
    )
    return [line for line in text.splitlines() if any(needle in line.lower() for needle in needles)]


def publication_for_target(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    for event in reversed(snapshot["api"]["telemetry"]["events"]):
        local_sync = event.get("local_sync")
        if event.get("kind") != "request_boundary" or not isinstance(local_sync, dict):
            continue
        for published in local_sync.get("published", []):
            if published.get("path") == TARGET:
                return published
    return None


def run(args: argparse.Namespace) -> None:
    source = Path(args.source).resolve()
    results = Path(args.results).resolve()
    results.mkdir(parents=True, exist_ok=True)
    log_path = results / "cpu-smoke-full.log"
    receipt_path = results / "cpu-smoke-receipt.json"
    status_dir = results / "process-state"
    status_dir.mkdir(parents=True, exist_ok=True)
    for stale in status_dir.glob("*.json"):
        stale.unlink()

    target = source / TARGET
    if not target.is_file():
        raise FileNotFoundError(f"release/source mismatch: runtime target absent: {target}")
    original_target_hash = sha256(target)
    baseline_hashes = python_source_hashes(source)
    manifest = {
        "schema_version": 1,
        "source_root": str(source),
        "reactive_paths": [
            TARGET,
            "vllm/v1/engine/async_llm.py",
        ],
        "files": [{"path": TARGET, "sha256": original_target_hash}],
    }
    manifest_path = results / "cpu-source-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "HMR_VLLM_ENABLE": "1",
            "HMR_VLLM_SOURCE_ROOT": str(source),
            "HMR_VLLM_SOURCE_SHA": f"installed-vllm-{args.vllm_version}",
            "HMR_VLLM_STATUS_DIR": str(status_dir),
            "HMR_VLLM_MANIFEST": str(manifest_path),
            "HMR_VLLM_AUTO_PATTERNS": TARGET,
            "HMR_VLLM_LAZY_PATTERNS": "",
            "HMR_VLLM_FORCED_DEPENDENTS": json.dumps({TARGET: [DEPENDENT]}),
            # sys.setprofile adds a Python callback to every call and made the
            # release-sized CPU import graph miss the 15 minute startup gate.
            # The unique in-function print is the direct traversal evidence.
            "HMR_VLLM_PROFILE": "0",
            "VLLM_PLUGINS": "hmr_vllm_probe",
            "VLLM_CPU_KVCACHE_SPACE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTH_ON_LINE_SHA": PYTH_SHA,
        }
    )
    command = [
        "vllm",
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
        "--worker-extension-cls",
        "hmr_vllm_probe.worker.HMRWorkerExtension",
        "--middleware",
        "hmr_vllm_probe.middleware.HMRBoundaryMiddleware",
    ]
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
            raise AssertionError("API process did not load the exact source manifest")
        if any(not worker["hmr"]["installed"] or worker["hmr"].get("manifest") != manifest for worker in before["workers"]):
            raise AssertionError("early injection/manifest was not active in every model worker")
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

            watcher_state = wait_manifest_pending(status_dir, before_identity["api_pid"])
            post_status, post_headers, post_payload = completion(base, args.model)
            if post_status != 200:
                raise AssertionError((post_status, post_payload))
            after = state(base)
            after_identity = identity(after)
            if after_identity != before_identity:
                raise AssertionError((before_identity, after_identity))
            publication = publication_for_target(after)
            if publication is None:
                raise AssertionError("next request boundary did not publish the manifest target")
            if DEPENDENT not in publication.get("forced_dependents_reexecuted", []):
                raise AssertionError(f"direct from-import dependent was not reexecuted: {publication}")
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
                    "watcher_pending_state": watcher_state,
                    "publication": publication,
                    "load_evidence_before_edit": load_evidence_before,
                    "load_evidence_after_edit": load_evidence_after_edit,
                    "marker_log_line": marker_line,
                    "assertions": {
                        "baseline_real_openai_inference_200": True,
                        "early_injection_active_in_api_and_model_worker": True,
                        "exactly_one_vllm_python_source_changed": True,
                        "exactly_one_unique_print_inserted": True,
                        "target_is_not_entrypoint": "entrypoint" not in TARGET,
                        "manifest_watcher_queued_target_before_next_inference": True,
                        "reactive_scope_exact_provider_and_direct_importer_only": True,
                        "direct_importer_explicitly_invalidated_and_reexecuted": True,
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
