#!/usr/bin/env python3
"""One-item real-SGLang CPU HMR smoke for the official CPU image."""

# pyright: reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportArgumentType=false, reportIndexIssue=false

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from mutations import MARKER, MutationSet, marker_lines, print_statement
from sglang_hmr import DEFAULT_RUNTIME
from sglang_hmr.runtime.scope import DEPENDENT, TARGET, sha256, write_manifest
from sglang_hmr.runtime.scope import build_manifest as build_manifest_obj

FUNCTION = "has_forward_context"
PYTH_SHA = "d410f975367e8a29b17183d108ef09a089e42b63"


def python_source_hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha256(path) for path in sorted((root / "python" / "sglang").rglob("*.py"))}


def request_json(base: str, method: str, path: str, body: dict | None = None, timeout: float = 300):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, method=method, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, {key.lower(): value for key, value in response.headers.items()}, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = raw.decode(errors="replace")
        return exc.code, {key.lower(): value for key, value in exc.headers.items()}, payload


def generate(base: str):
    return request_json(base, "POST", "/generate", {"text": "The capital of France is", "sampling_params": {"max_new_tokens": 4, "temperature": 0}})


def server_info(base: str) -> dict[str, Any]:
    status, _, payload = request_json(base, "GET", "/server_info")
    if status != 200 or not isinstance(payload, dict):
        raise AssertionError((status, payload))
    return payload


def probe(base: str) -> dict[str, Any]:
    """The scheduler's own readback, injected by the example's SGLang plugin.

    `/server_info` returns one entry per DP rank; this smoke runs a single scheduler,
    so exactly one entry must carry the probe. A missing probe means the plugin never
    loaded in the scheduler, which would make every identity assertion below vacuous.
    """
    info = server_info(base)
    states = info.get("internal_states") or []
    probes = [state["hmr_probe"] for state in states if isinstance(state, dict) and "hmr_probe" in state]
    errors = [state["hmr_probe_error"] for state in states if isinstance(state, dict) and "hmr_probe_error" in state]
    if errors:
        raise AssertionError(f"scheduler probe failed: {errors}")
    if len(probes) != 1:
        raise AssertionError(f"expected exactly one scheduler probe, got {len(probes)}: {states}")
    return probes[0]


class ServerProcess(Protocol):
    """The only part of `Popen` the launch/teardown helpers touch, so tests can supply a real double."""

    pid: int
    returncode: int | None

    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...


def wait_ready(base: str, process: ServerProcess, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"SGLang exited during startup with {process.returncode}")
        try:
            status, _, payload = request_json(base, "GET", "/health_generate", timeout=5)
            if status == 200:
                return
            last = payload
        except (OSError, TimeoutError) as exc:
            last = repr(exc)
        time.sleep(1)
    raise TimeoutError(f"SGLang did not become ready: {last}")


def identity(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Everything a weight reload or a restart would change, and nothing a swap does."""
    return {key: snapshot[key] for key in ("pid", "ppid", "model_runner_id", "model_id", "model_class_id", "model_class", "first_parameter_data_ptr", "weight_load_time")}


def load_lines(text: str) -> list[str]:
    needles = ("load weight begin", "load weight end", "loading model weights", "weight loading")
    return [line for line in text.splitlines() if any(needle in line.lower() for needle in needles)]


def read_process_cmdline(pid: int) -> list[str]:
    """Read the process's actual command line from /proc, NUL-split.

    This is the only way to verify what the listener process exec'd into, since the
    scheduler's telemetry.orig_argv comes from a multiprocessing spawn child and does
    not reflect the actual `sglang serve ...` invocation.
    """
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    if not cmdline_path.exists():
        raise RuntimeError(f"/proc not available or process {pid} does not exist: {cmdline_path} is missing")
    raw = cmdline_path.read_bytes()
    return [token.decode("utf-8", errors="replace") for token in raw.split(b"\x00") if token]


def assert_official_argv(pid: int, expected_sglang_argv: list[str]) -> list[str]:
    """Verify the listener process exec'd into the official sglang console script with the expected argv.

    Returns the full cmdline for receipt recording. The exec replaces this process with
    `sglang serve ...`, so argv[0] is 'sglang' and argv[1:] must match the forwarded SGLang argv.
    """
    cmdline = read_process_cmdline(pid)
    if not cmdline:
        raise AssertionError(f"process {pid} has an empty cmdline")
    if cmdline[0] == "sglang":
        forwarded = cmdline[1:]
    elif len(cmdline) >= 2 and Path(cmdline[0]).name.startswith("python") and Path(cmdline[1]).name == "sglang":
        forwarded = cmdline[2:]
    else:
        raise AssertionError(f"the server was not exec'd into the official sglang console script: argv={cmdline!r}")
    if any(token.startswith("--hmr-") for token in cmdline):
        raise AssertionError(f"HMR-only options leaked into the official sglang argv: {cmdline}")
    if forwarded != expected_sglang_argv:
        raise AssertionError(f"forwarded argv differs from the official SGLang argv: {forwarded} != {expected_sglang_argv}")
    return cmdline


def published_record(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """The runtime's own `published` event for the target, from the scheduler's telemetry."""
    for event in reversed(snapshot["telemetry"]["events"]):
        if event.get("kind") == "published" and event.get("path") == TARGET:
            return event
    return None


def queued_before(snapshot: dict[str, Any], publication: dict[str, Any]) -> dict[str, Any] | None:
    """The watcher's own `source_change`, required to precede the publication."""
    for event in snapshot["telemetry"]["events"]:
        if event.get("kind") == "source_change" and event.get("path") == TARGET and event["t"] <= publication["t"]:
            return event
    return None


def rejected_records(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [event for event in snapshot["telemetry"]["events"] if event.get("kind") in {"rejected", "watcher_failed"}]


def wait_published(base: str, timeout: float = 180) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Poll the scheduler's HMR state until the packaged runtime publishes the target.

    SGLang has no request-boundary hook, so the watcher thread publishes on its own and
    this only observes it. Nothing here triggers publication, and no request is sent in
    the meantime: the next real `/generate` after this returns is the first request that
    can observe the new code.
    """
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        snapshot = probe(base)["hmr"]
        publication = published_record(snapshot)
        if publication is not None:
            queued = queued_before(snapshot, publication)
            if queued is None:
                raise AssertionError(f"the target was published with no preceding watcher event: {publication}")
            return snapshot, publication, queued
        if rejected := rejected_records(snapshot):
            raise AssertionError(f"the runtime refused to publish {TARGET}: {rejected}")
        last = {"pending": snapshot["pending"], "installed": snapshot["installed"]}
        time.sleep(0.5)
    raise TimeoutError(f"the packaged runtime did not publish {TARGET}: {last}")


def resolve_paths(source: str, results: str) -> tuple[Path, Path]:
    """Both arrive as possibly-relative CLI strings, but every receipt field, the manifest's
    own `source_root`, and the runtime's `--hmr-source-root` are compared against resolved
    paths. Absolutizing here is what makes those comparisons mean the same thing."""
    return Path(source).resolve(), Path(results).resolve()


def preflight(source: Path) -> tuple[Path, str]:
    """Refuse to launch unless the mutation target is really there and the wrapper is really installed."""
    target = source / TARGET
    if not target.is_file():
        raise FileNotFoundError(f"release/source mismatch: runtime target absent: {target}")
    launcher = shutil.which("sglang-hmr")
    if launcher is None:
        raise FileNotFoundError("the `sglang-hmr` console script is not installed on PATH")
    return target, launcher


def build_manifest(source: Path) -> dict[str, Any]:
    """The package's own canonical scope, serialized exactly as the runtime will read it back.

    Deliberately not a second hand-written copy: `smoke.py` asserting equality against
    `state()["manifest"]` only means something while both sides come from `Manifest.as_dict`.
    """
    return build_manifest_obj(source).as_dict()


def build_hmr_argv(source: Path, manifest_path: Path) -> list[str]:
    return ["--hmr-source-root", str(source), "--hmr-manifest", str(manifest_path)]


def build_sglang_argv(model: str, port: int) -> list[str]:
    """Official SGLang argv only. Nothing here is HMR-specific, and `serve` stays first."""
    return ["serve", model, "--host", "127.0.0.1", "--port", str(port), "--dtype", "float32", "--max-total-tokens", "256", "--mem-fraction-static", "0.7", "--device", "cpu"]


def image_metadata(args: argparse.Namespace) -> dict[str, Any]:
    """What pins this run to one official image and one SGLang distribution."""
    return {
        "official_image": args.image,
        "official_image_id": args.image_id,
        "official_image_digest": args.image_digest,
        "sglang_distribution_version": args.sglang_version,
        "installed_distribution_source": args.installed_source,
        "pyth_on_line_sha": PYTH_SHA,
        "pyth_core_path": args.pyth_core_path,
        "pyth_core_sha256": args.pyth_core_sha256,
    }


def teardown(process: ServerProcess, term_timeout: float = 60, kill_timeout: float = 30) -> str | None:
    """Kill the whole group, tolerating the race where it exits between `poll` and the signal.

    The launcher exec'd into `sglang`, which spawns its own children, so the group has to go
    or a scheduler survives the container. `ProcessLookupError` means the group is already
    gone, which is the outcome we wanted.

    Never raises: teardown runs in `run`'s `finally`, where an exception would both lose the
    receipt and replace the real smoke failure with a cleanup error. The problem is reported
    as a returned string instead, which the caller records in the receipt.
    """
    try:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return None
        try:
            process.wait(timeout=term_timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):  # the group may have exited between the wait and the kill
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=kill_timeout)
    except subprocess.TimeoutExpired as exc:
        return f"the process group survived SIGKILL: {type(exc).__name__}: {exc}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def write_receipt(path: Path, receipt: dict[str, Any]) -> Path:
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run(args: argparse.Namespace) -> None:
    source, results = resolve_paths(args.source, args.results)
    results.mkdir(parents=True, exist_ok=True)
    log_path = results / "cpu-smoke-full.log"
    receipt_path = results / "cpu-smoke-receipt.json"

    target, launcher = preflight(source)
    original_target_hash = sha256(target)
    baseline_hashes = python_source_hashes(source)
    manifest_obj = build_manifest_obj(source)
    manifest = manifest_obj.as_dict()
    manifest_path = write_manifest(manifest_obj, results / "cpu-source-manifest.json")

    env = os.environ.copy()
    inherited_plugins = env.get("SGLANG_PLUGINS", "").strip()
    probe_plugin = "hmr_sglang_probe"
    if inherited_plugins:
        plugins_list = [p.strip() for p in inherited_plugins.split(",") if p.strip()]
        if probe_plugin not in plugins_list:
            plugins_list.append(probe_plugin)
        merged_plugins = ",".join(plugins_list)
    else:
        merged_plugins = probe_plugin
    env.update({"SGLANG_PLUGINS": merged_plugins, "PYTHONUNBUFFERED": "1", "PYTH_ON_LINE_SHA": PYTH_SHA})
    hmr_argv = build_hmr_argv(source, manifest_path)
    sglang_argv = build_sglang_argv(args.model, args.port)
    command = [launcher, *hmr_argv, *sglang_argv]
    process: subprocess.Popen | None = None
    failure: BaseException | None = None  # set in `except` so `finally` can tell "clean run" from "already failing"
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "status": "failed",
        "started_at": time.time(),
        **image_metadata(args),
        "runtime_source_root": str(source),
        "runtime_source_resolution": (
            "The official v0.5.16 wheel lives under site-packages, which pyth-on-line d410f975 excludes by design. The container copies that installed sglang package byte-for-byte to an external runtime source root and imports the copy first; the smoke does not assume that SGLang source is present in this repository."
        ),
        "reactive_scope": (
            "Only the manifest-listed provider and its actual direct from-import consumer are reactive. This proves this one request-path Python function/dependency chain, not arbitrary SGLang modules."
        ),
        "observability_instrumentation": (
            "Setup-only, and separate from the mutation under test: the example registers one AFTER hook on Scheduler.get_internal_state through SGLang's own `sglang.srt.plugins` entry-point group, so no SGLang source file is edited to read identity back. The single post-baseline mutation is the print inserted into has_forward_context."
        ),
        "target": {"path": TARGET, "function": FUNCTION, "original_sha256": original_target_hash},
        "marker": MARKER,
        "model": args.model,
        "command": command,
        "launcher": {
            "console_script": launcher,
            "hmr_only_argv": hmr_argv,
            "official_sglang_argv": sglang_argv,
            "note": "the smoke never runs `sglang` directly: `sglang-hmr` strips every --hmr-* option into HMR_SGLANG_* and execs the official CLI in place",
        },
        "runtime_under_test": {
            "runtime": DEFAULT_RUNTIME,
            "runtime_source": "CLI default: --hmr-runtime is not passed",
            "probe_scope": "hmr_sglang_probe is observability-only: it installs no watcher, publishes nothing, and holds no HMR state. Every HMR decision in this run is made by sglang_hmr.",
        },
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "log_path": str(log_path),
        "receipt_path": str(receipt_path),
        "assertions": {},
    }

    try:
        with log_path.open("wb", buffering=0) as log:
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        receipt["launcher_pid"] = process.pid
        base = f"http://127.0.0.1:{args.port}"
        wait_ready(base, process, args.startup_timeout)

        baseline_status, _, baseline_response = generate(base)
        if baseline_status != 200 or not isinstance(baseline_response, dict):
            raise AssertionError((baseline_status, baseline_response))
        baseline_probe = probe(base)
        baseline_identity = identity(baseline_probe)
        baseline_hmr = baseline_probe["hmr"]
        if not baseline_hmr["installed"]:
            raise AssertionError("the packaged runtime is not installed in the scheduler process")
        if baseline_hmr.get("manifest") != manifest:
            raise AssertionError(f"the packaged runtime did not load the exact source manifest: {baseline_hmr.get('manifest')}")
        # The scheduler's live module must come from the source root, not from the
        # installed site-packages copy. Without this, an edit could publish "successfully"
        # against a tree the running process never imported.
        live_file = baseline_probe["modules"].get("sglang.srt.model_executor.forward_context", {}).get("module_file")
        if live_file is None or Path(live_file).resolve() != target.resolve():
            raise AssertionError(f"the live module came from {live_file}, not from the source root target {target}")
        # `sglang-hmr` exec'd into this PID, so the listener's own cmdline is proof of both
        # the `--hmr-*` strip and the handover to the official console script.
        listener_cmdline = assert_official_argv(process.pid, sglang_argv)
        # The scheduler probe's telemetry.orig_argv is the multiprocessing spawn argv, not
        # the listener's launch command, so it's stored separately for observability only.
        scheduler_probe_argv = baseline_hmr["telemetry"]["orig_argv"]
        before_log = log_path.read_text(encoding="utf-8", errors="replace")
        load_evidence_before = load_lines(before_log)

        with MutationSet(source) as edits:
            statement = print_statement(MARKER)
            inserted_line = edits.insert_statement(TARGET, FUNCTION, statement)
            mutated_hash = sha256(target)
            if mutated_hash == original_target_hash:
                raise AssertionError("target bytes did not change")
            changed_while_mutated = sorted(key for key, digest in python_source_hashes(source).items() if baseline_hashes.get(key) != digest)
            if changed_while_mutated != [TARGET]:
                raise AssertionError(f"expected exactly one changed Python source, got {changed_while_mutated}")
            mutated_text = target.read_text(encoding="utf-8")
            if mutated_text.count(MARKER) != 1 or mutated_text.count(statement) != 1:
                raise AssertionError("mutation did not insert exactly one unique print")

            hmr_at_publication, publication, queued_event = wait_published(base)
            if DEPENDENT not in publication.get("forced_dependents_reexecuted", []):
                raise AssertionError(f"direct from-import dependent was not reexecuted: {publication}")
            # The watcher published, but nothing has called the new code yet: the marker
            # must be absent here, or it did not come from the request below.
            log_before_request = log_path.read_text(encoding="utf-8", errors="replace")
            if marker_lines(log_before_request, MARKER):
                raise AssertionError("the marker appeared before the post-publication request")

            post_status, _, post_response = generate(base)
            if post_status != 200 or not isinstance(post_response, dict):
                raise AssertionError((post_status, post_response))
            post_probe = probe(base)
            post_identity = identity(post_probe)
            if post_identity != baseline_identity:
                raise AssertionError((baseline_identity, post_identity))

            log_after = log_path.read_text(encoding="utf-8", errors="replace")
            expected_marker = f"{MARKER} pid={baseline_identity['pid']}"
            observed = marker_lines(log_after, MARKER)
            if not any(expected_marker in line for line in observed):
                raise AssertionError(f"marker missing from the scheduler process that owns the model: expected {expected_marker!r}, saw {observed}")
            post_edit_log = log_after[len(before_log) :]
            load_evidence_after_edit = load_lines(post_edit_log)
            if load_evidence_after_edit:
                raise AssertionError(f"model reload evidence appeared after edit: {load_evidence_after_edit}")

            receipt.update(
                {
                    "status": "passed",
                    "baseline": {"http_status": baseline_status, "text": baseline_response.get("text"), "meta_info": baseline_response.get("meta_info")},
                    "post_edit": {"http_status": post_status, "text": post_response.get("text"), "meta_info": post_response.get("meta_info")},
                    "identity_before": baseline_identity,
                    "identity_after": post_identity,
                    "modules_before": baseline_probe["modules"],
                    "modules_after": post_probe["modules"],
                    "live_module_file": live_file,
                    "inserted_line": inserted_line,
                    "mutated_sha256": mutated_hash,
                    "changed_python_sources_while_mutated": changed_while_mutated,
                    "watcher_queued_event": queued_event,
                    "publication": publication,
                    "hmr_state_at_publication": hmr_at_publication,
                    "hmr_state_after_request": post_probe["hmr"],
                    "load_evidence_before_edit": load_evidence_before,
                    "load_evidence_after_edit": load_evidence_after_edit,
                    "marker_log_lines": observed,
                    "listener_cmdline": listener_cmdline,
                    "scheduler_probe_argv": scheduler_probe_argv,
                    "assertions": {
                        "service_started_through_sglang_hmr_console_script": True,
                        "hmr_options_stripped_before_official_sglang_exec": True,
                        "packaged_default_runtime_not_overridden": "--hmr-runtime" not in command,
                        "baseline_real_generate_200": True,
                        "early_injection_active_in_scheduler": True,
                        "live_module_imported_from_source_root": True,
                        "exactly_one_sglang_python_source_changed": True,
                        "exactly_one_unique_print_inserted": True,
                        "target_is_not_entrypoint": "entrypoint" not in TARGET,
                        "watcher_queued_target_before_publication": True,
                        "reactive_scope_exact_provider_and_direct_importer_only": True,
                        "direct_importer_explicitly_invalidated_and_reexecuted": True,
                        "marker_absent_until_the_next_real_request": True,
                        "next_real_generate_200": True,
                        "marker_from_the_scheduler_that_owns_the_model": True,
                        "same_listener_and_scheduler_pids": True,
                        "same_model_object_class_and_parameter_pointer": True,
                        "same_weight_load_time": True,
                        "no_model_reload_log_after_edit": True,
                        "server_process_not_restarted": process.poll() is None,
                    },
                }
            )

        restored_hash = sha256(target)
        restored_changes = sorted(key for key, digest in python_source_hashes(source).items() if baseline_hashes.get(key) != digest)
        receipt["restored_sha256"] = restored_hash
        receipt["changed_python_sources_after_restore"] = restored_changes
        receipt["assertions"]["edited_bytes_restored"] = restored_hash == original_target_hash and not restored_changes
        if not receipt["assertions"]["edited_bytes_restored"]:
            receipt["status"] = "failed"
            raise AssertionError((original_target_hash, restored_hash, restored_changes))
    except BaseException as exc:
        failure = exc
        receipt["status"] = "failed"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        # Teardown happens in `finally`, so the server is still up here: a failed receipt
        # that carries no runtime state is worth almost nothing once the container is gone.
        if process is not None and process.poll() is None:
            try:
                receipt["state_at_failure"] = probe(f"http://127.0.0.1:{args.port}")
            except Exception as probe_error:
                receipt["state_at_failure_error"] = f"{type(probe_error).__name__}: {probe_error}"
        raise
    finally:
        # Nothing in here may raise before `write_receipt`: this is the only place the receipt
        # is written, and a lost receipt makes the whole run unreportable once the container is gone.
        teardown_error: str | None = None
        try:
            if process is not None and process.poll() is None:
                teardown_error = teardown(process)
        except Exception as exc:  # `poll` itself can fail; the receipt matters more than this detail
            teardown_error = f"{type(exc).__name__}: {exc}"
        try:
            receipt["server_exit_code_after_teardown"] = None if process is None else process.returncode
            receipt["server_stopped"] = process is None or process.poll() is not None
            receipt["source_restored"] = sha256(target) == original_target_hash
        except Exception as exc:
            receipt["teardown_state_error"] = f"{type(exc).__name__}: {exc}"
        if teardown_error is not None:
            receipt["teardown_error"] = teardown_error
            receipt["status"] = "failed"  # a surviving scheduler invalidates the run, passed assertions or not
        receipt["finished_at"] = time.time()
        write_receipt(receipt_path, receipt)
        # A teardown failure is real, but it must never replace the smoke failure already
        # propagating: that one is why the run is being reported at all.
        if failure is None and teardown_error is not None:
            raise RuntimeError(f"teardown failed: {teardown_error}")

    print(json.dumps({"status": receipt["status"], "receipt_path": str(receipt_path), "log_path": str(log_path)}))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="SGLang source root (containing python/sglang)")
    parser.add_argument("--results", required=True, help="Directory for receipt and logs")
    parser.add_argument("--model", default="facebook/opt-125m", help="Model path or HF ID")
    parser.add_argument("--port", type=int, default=31000, help="SGLang API port")
    parser.add_argument("--startup-timeout", type=float, default=900)
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--sglang-version", required=True)
    parser.add_argument("--installed-source", required=True)
    parser.add_argument("--pyth-core-path", required=True)
    parser.add_argument("--pyth-core-sha256", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
