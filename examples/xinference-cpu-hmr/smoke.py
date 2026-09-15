#!/usr/bin/env python3
"""One-item real-Xinference CPU HMR smoke for the official CPU image."""

# pyright: reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportArgumentType=false, reportIndexIssue=false, reportMissingImports=false

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
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

from mutations import MARKER, MutationSet, marker_lines, print_statement
from xinference_hmr import DEFAULT_RUNTIME
from xinference_hmr.runtime.scope import TARGET, sha256, write_manifest
from xinference_hmr.runtime.scope import build_manifest as build_manifest_obj

FUNCTION = "batch_inference_one_step"
TARGET_MODULE = TARGET.removesuffix(".py").replace("/", ".")
MODEL_UID = "hmr-tiny"
MODEL_TYPE = "LLM"
CUSTOM_FAMILY = "hmr-tiny-gpt2"
PYTH_REVISION = "d410f975367e8a29b17183d108ef09a089e42b63"
PYTH_CORE_SHA256 = "e89f00a3aaf9ad9451e3fd4e63680d40783a08452f142f494e23f962d0e544eb"
XINFERENCE_IMAGE_HEAD = "99868ea70d5e17267532df64ae30de40007091b4"


def python_source_hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha256(path) for path in sorted((root / "xinference").rglob("*.py"))}


def request_json(base: str, method: str, path: str, body: dict | None = None, timeout: float = 600):
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


def completion(base: str, prompt: str = "Hello"):
    """A real OpenAI-compatible completion against the loaded model."""
    return request_json(base, "POST", "/v1/completions", {"model": MODEL_UID, "prompt": prompt, "max_tokens": 5, "temperature": 0})


def chat(base: str, content: str = "Hello"):
    """A real OpenAI-compatible chat completion against the loaded model."""
    return request_json(base, "POST", "/v1/chat/completions", {"model": MODEL_UID, "messages": [{"role": "user", "content": content}], "max_tokens": 5, "temperature": 0})


def response_text(payload: dict[str, Any]) -> str | None:
    """The generated text, from whichever of the two response shapes this run used."""
    choice = (payload.get("choices") or [{}])[0]
    return (choice.get("message") or {}).get("content") if "message" in choice else choice.get("text")


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
            raise RuntimeError(f"Xinference exited during startup with {process.returncode}")
        try:
            status, _, payload = request_json(base, "GET", "/v1/models", timeout=10)
            if status == 200:
                return
            last = payload
        except (OSError, TimeoutError) as exc:
            last = repr(exc)
        time.sleep(1)
    raise TimeoutError(f"Xinference did not become ready: {last}")


def register_family(base: str, model_path: str) -> dict[str, Any]:
    """Register a chat-only custom LLM family over the same local weights.

    Needed because the builtin `gpt-2` family declares `generate` only, so a chat request
    against it is rejected by Xinference before any model code runs -- a 500 that says
    nothing about HMR.

    Chat-only, not `["generate", "chat"]`: a family with `chat` instantiates
    `PytorchChatModel`, whose `prepare_batch_inference` runs *every* request through
    `_get_full_prompt`, so a raw-string `/v1/completions` prompt reaches
    `convert_messages_with_content_list_to_str_conversion` and raises
    `'str' object has no attribute 'get'` (observed). That is Xinference's own behaviour for
    chat models, not an HMR failure, so the two endpoints are exercised by two separate
    single-model runs (`--mode`) rather than papered over in one.

    This is setup, not the mutation under test: it uses Xinference's own public registration
    API and edits no source.
    """
    family = {
        "version": 2,
        "model_name": CUSTOM_FAMILY,
        "model_lang": ["en"],
        "model_ability": ["chat"],
        "model_description": "Tiny GPT-2 used by the xinference-hmr CPU smoke.",
        "model_family": CUSTOM_FAMILY,
        "context_length": 512,
        # Minimal, and deliberately not a real chat format: the smoke asserts an HTTP 200 and a
        # completion from the loaded weights, not conversational quality from a 5-layer random model.
        "chat_template": "{% for message in messages %}{{ message['content'] }}\n{% endfor %}",
        "stop_token_ids": [],
        "stop": [],
        "model_specs": [
            {
                "model_format": "pytorch",
                "model_size_in_billions": 1,
                "quantization": "none",
                "model_uri": f"file://{model_path}",
                "model_src": {"huggingface": {"quantizations": ["none"], "model_uri": f"file://{model_path}"}},
            }
        ],
    }
    status, _, payload = request_json(base, "POST", f"/v1/model_registrations/{MODEL_TYPE}", {"model": json.dumps(family), "persist": False})
    if status != 200:
        raise AssertionError(f"custom family registration failed: {status} {payload}")
    return family


def launch_model(base: str, model_path: str, timeout: float, *, chat: bool) -> dict[str, Any]:
    """Launch the tiny transformers model and require a real ModelActor to come up.

    `enable_virtual_env=false` is essential, not cosmetic: with a virtual environment the
    sub-pool is started under a *different* interpreter, which never sees this run's
    `PYTHONPATH` shim, so HMR would silently not be installed in the process that owns
    the model. The package's README states this as a scope limit.
    """
    body = {
        "model_uid": MODEL_UID,
        "model_name": CUSTOM_FAMILY if chat else "gpt-2",
        "model_engine": "Transformers",
        "model_format": "pytorch",
        "model_size_in_billions": 1 if chat else "1_5",
        "quantization": "none",
        "model_path": model_path,
        "enable_virtual_env": False,
    }
    status, _, payload = request_json(base, "POST", "/v1/models", body, timeout=timeout)
    if status != 200:
        raise AssertionError(f"launch failed: {status} {payload}")
    status, _, listing = request_json(base, "GET", "/v1/models")
    if status != 200 or not isinstance(listing, dict):
        raise AssertionError(f"/v1/models after launch: {status} {listing}")
    entries = listing.get("data") or []
    if not entries:
        raise AssertionError("the model list is empty after a successful launch: no ModelActor was created")
    entry = next((item for item in entries if item.get("id") == MODEL_UID), None)
    if entry is None:
        raise AssertionError(f"{MODEL_UID} is absent from the model list: {entries}")
    return entry


def read_records(path: Path) -> list[dict[str, Any]]:
    """Every line the actor-process probe has written so far."""
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        with contextlib.suppress(json.JSONDecodeError):
            records.append(json.loads(line))
    return records


def last_record(records: list[dict[str, Any]], kind: str, **match) -> dict[str, Any] | None:
    for record in reversed(records):
        if record.get("kind") == kind and all(record.get(key) == value for key, value in match.items()):
            return record
    return None


def identity(record: dict[str, Any]) -> dict[str, Any]:
    """Everything a reload, a relaunch, or an actor rebuild would change, and nothing a source swap does."""
    keys = ("pid", "actor_id", "actor_class_id", "model_id", "model_class", "model_class_id", "torch_module_id", "tokenizer_id", "batch_scheduler_id", "param_count", "param_ptrs_head")
    missing = [key for key in keys if key not in record]
    if missing:
        raise AssertionError(f"probe record is missing identity fields {missing}: {record}")
    return {key: record[key] for key in keys}


def load_lines(text: str) -> list[str]:
    needles = ("loading weights", "load weight", "loading checkpoint", "modelactor(", "loading configuration file", "launch_builtin_model")
    return [line for line in text.splitlines() if any(needle in line.lower() for needle in needles)]


def read_process_cmdline(pid: int) -> list[str]:
    """Read the process's actual command line from /proc, NUL-split.

    This is the only way to verify what the launcher process exec'd into: the sub-pool's
    own `orig_argv` is a xoscar spawn argv and says nothing about the official launch.
    """
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    if not cmdline_path.exists():
        raise RuntimeError(f"/proc not available or process {pid} does not exist: {cmdline_path} is missing")
    raw = cmdline_path.read_bytes()
    return [token.decode("utf-8", errors="replace") for token in raw.split(b"\x00") if token]


def assert_official_argv(pid: int, expected_argv: list[str]) -> list[str]:
    """Verify the launcher exec'd into the official console script with the expected argv.

    Both argv forms are accepted: the console script directly, and the interpreter followed
    by the exact console script path, which is what a `#!` script looks like in /proc.
    """
    cmdline = read_process_cmdline(pid)
    if not cmdline:
        raise AssertionError(f"process {pid} has an empty cmdline")
    if Path(cmdline[0]).name == "xinference-local":
        forwarded = cmdline[1:]
    elif len(cmdline) >= 2 and Path(cmdline[0]).name.startswith("python") and Path(cmdline[1]).name == "xinference-local":
        forwarded = cmdline[2:]
    else:
        raise AssertionError(f"the server was not exec'd into the official xinference-local console script: argv={cmdline!r}")
    if any(token.startswith("--hmr-") for token in cmdline):
        raise AssertionError(f"HMR-only options leaked into the official Xinference argv: {cmdline}")
    if forwarded != expected_argv:
        raise AssertionError(f"forwarded argv differs from the official Xinference argv: {forwarded} != {expected_argv}")
    return cmdline


def hmr_events(records: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """HMR telemetry events, read out of the actor-boundary records the probe captured.

    The boundary's `sync` result is embedded in each `actor_boundary` telemetry event, so the
    runtime's own account of what it published is recovered from the actor process itself
    rather than from anything the REST layer reports.
    """
    out = []
    for record in records:
        for event in record.get("hmr_events") or ():
            if event.get("kind") == kind:
                out.append(event)
    return out


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
    launcher = shutil.which("xinference-hmr")
    if launcher is None:
        raise FileNotFoundError("the `xinference-hmr` console script is not installed on PATH")
    return target, launcher


def build_hmr_argv(source: Path, manifest_path: Path) -> list[str]:
    return ["--hmr-source-root", str(source), "--hmr-manifest", str(manifest_path)]


def build_xinference_argv(port: int) -> list[str]:
    """Official Xinference argv only. Nothing here is HMR-specific."""
    return ["--host", "127.0.0.1", "--port", str(port)]


def image_metadata(args: argparse.Namespace) -> dict[str, Any]:
    """What pins this run to one official image and one Xinference source revision."""
    return {
        "official_image": args.image,
        "official_image_id": args.image_id,
        "official_image_digest": args.image_digest,
        "xinference_distribution_version": args.xinference_version,
        "xinference_image_commit": args.xinference_commit,
        "xinference_image_git_head": args.xinference_git_head,
        "installed_distribution_source": args.installed_source,
        "hmr_core_version": args.hmr_core_version,
    }


def hmr_provenance(args: argparse.Namespace) -> dict[str, Any]:
    """Verify the installed core against a host-computed digest and its PEP 610 source."""
    from reactivity.hmr import core

    direct_text = metadata.distribution("hmr").read_text("direct_url.json")
    if not direct_text:
        raise AssertionError("hmr has no direct_url.json; a floating index install is not accepted")
    direct_url = json.loads(direct_text)
    core_path = Path(core.__file__).resolve()
    actual = sha256(core_path)
    source_url = str(direct_url.get("url", ""))
    vcs = direct_url.get("vcs_info") or {}
    source_matches = PYTH_REVISION in source_url or vcs.get("commit_id") == PYTH_REVISION
    if not source_matches:
        raise AssertionError(f"hmr direct source does not carry {PYTH_REVISION}: {direct_url}")
    if args.pyth_revision != PYTH_REVISION or args.expected_core_sha256 != PYTH_CORE_SHA256 or actual != args.expected_core_sha256:
        raise AssertionError(f"hmr core mismatch: revision={args.pyth_revision}, expected={args.expected_core_sha256}, actual={actual}")
    if args.hmr_core_version != "0.7.6.2":
        raise AssertionError(f"unexpected hmr version for {PYTH_REVISION}: {args.hmr_core_version}")
    return {
        "pinned_revision": PYTH_REVISION,
        "direct_url": direct_url,
        "core_path": str(core_path),
        "core_sha256": actual,
        "expected_core_sha256": args.expected_core_sha256,
        "version": args.hmr_core_version,
    }


def teardown(process: ServerProcess, term_timeout: float = 120, kill_timeout: float = 60) -> str | None:
    """Kill the whole group, tolerating the race where it exits between `poll` and the signal.

    The launcher exec'd into `xinference-local`, which spawns a supervisor/worker process and
    one sub-pool per model, so the group has to go or a sub-pool survives the container.
    `ProcessLookupError` means the group is already gone, which is the outcome we wanted.

    Never raises: teardown runs in `run`'s `finally`, where an exception would both lose the
    receipt and replace the real smoke failure with a cleanup error.
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
    record_path = Path(args.probe_record).resolve()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text("", encoding="utf-8")  # a stale file from an earlier run would be read as this run's evidence

    target, launcher = preflight(source)
    provenance = hmr_provenance(args)
    if args.xinference_git_head != XINFERENCE_IMAGE_HEAD:
        raise AssertionError(f"official image source mismatch: {args.xinference_git_head} != {XINFERENCE_IMAGE_HEAD}")
    original_target_hash = sha256(target)
    baseline_hashes = python_source_hashes(source)
    manifest_obj = build_manifest_obj(source)
    manifest = manifest_obj.as_dict()
    manifest_path = write_manifest(manifest_obj, results / "cpu-source-manifest.json")

    # One model, one endpoint per run. `batch_inference_one_step` is the same target on both
    # paths, so each mode is a complete proof on its own; running them separately is what keeps
    # every identity assertion about a single actor instead of two.
    chat_mode = args.mode == "chat"
    endpoint = "/v1/chat/completions" if chat_mode else "/v1/completions"
    infer = chat if chat_mode else completion
    probe_method = "chat" if chat_mode else "generate"

    env = os.environ.copy()
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "HMR_XINFERENCE_PROBE_RECORD": str(record_path),
            # The probe's own `sitecustomize`. `xinference-hmr` prepends its shim ahead of this,
            # and the shim chains to whatever `sitecustomize` it shadowed -- which is this one.
            "PYTHONPATH": os.pathsep.join([args.probe_dir, *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]),
            "XINFERENCE_AUTH_ADVANCED": "false",
            "XINFERENCE_ENABLE_VIRTUAL_ENV": "0",
        }
    )
    hmr_argv = build_hmr_argv(source, manifest_path)
    xinference_argv = build_xinference_argv(args.port)
    command = [launcher, *hmr_argv, "local", *xinference_argv]
    process: subprocess.Popen | None = None
    failure: BaseException | None = None  # set in `except` so `finally` can tell "clean run" from "already failing"
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "status": "failed",
        "started_at": time.time(),
        **image_metadata(args),
        "hmr_provenance": provenance,
        "runtime_source_root": str(source),
        "runtime_source_resolution": (
            "The official CPU image installs xinference from the local directory /opt/inference, and xoscar starts each model sub-pool as `python -m xoscar.backends.indigen ...` with the worker's cwd (/opt/inference) inherited, so `-m` puts that source tree at sys.path[0] and the sub-pool imports xinference from it while the REST process, launched as a console script, imports from site-packages. The two copies are byte-identical on a fresh image, so this smoke asserts the live module file explicitly rather than assuming which copy runs."
        ),
        "reactive_scope": (
            "Only the manifest-listed provider is reactive. Its consumer, PytorchModel.batch_inference, performs a function-local `from .utils import batch_inference_one_step` on every batch step, so the existing model object picks up the re-executed module with no forced dependent and no class replacement. This proves this one request-path Python function, not arbitrary Xinference modules."
        ),
        "observability_instrumentation": (
            "Setup-only, and separate from the mutation under test: the example ships its own `sitecustomize`, reached through the wrapper's documented `sitecustomize` chaining, which wraps ModelActor.load/generate/chat to record identity. No Xinference source file is edited to read identity back. The single post-baseline mutation is the print inserted into batch_inference_one_step."
        ),
        "publication_boundary": (
            "xinference_hmr wraps ModelActor.__on_receive__ inside the sub-pool process that owns the loaded model. Publication happens there, at the actor's own request boundary -- not in the REST process, not in the supervisor, and not on a watcher thread's own schedule."
        ),
        "target": {"path": TARGET, "function": FUNCTION, "original_sha256": original_target_hash},
        "marker": MARKER,
        "model": {"uid": MODEL_UID, "name": CUSTOM_FAMILY if chat_mode else "gpt-2", "engine": "Transformers", "path": args.model_path},
        "mode": {"mode": args.mode, "endpoint": endpoint, "actor_method": probe_method},
        "command": command,
        "launcher": {
            "console_script": launcher,
            "hmr_only_argv": hmr_argv,
            "official_xinference_argv": xinference_argv,
            "note": "the smoke never runs `xinference-local` directly: `xinference-hmr` strips every --hmr-* option into HMR_XINFERENCE_* and execs the official CLI in place",
        },
        "runtime_under_test": {
            "runtime": DEFAULT_RUNTIME,
            "runtime_source": "CLI default: --hmr-runtime is not passed",
            "probe_scope": "the example's sitecustomize is observability-only: it installs no watcher, publishes nothing, and holds no HMR state. Every HMR decision in this run is made by xinference_hmr.",
        },
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "log_path": str(log_path),
        "receipt_path": str(receipt_path),
        "probe_record_path": str(record_path),
        "assertions": {},
    }

    try:
        with log_path.open("wb", buffering=0) as log:
            # `cwd=source` is load-bearing, not tidiness. The model sub-pool is started as
            # `python -m xoscar.backends.indigen ...` with the worker's cwd inherited, and `-m`
            # puts that cwd on `sys.path[0]`. Launch from anywhere else and the sub-pool imports
            # xinference from site-packages instead of the watched tree, so the watcher would see
            # every edit and publish none of them -- which looks exactly like a marker that never
            # arrives. The package re-checks this in-process and refuses to run if they disagree.
            process = subprocess.Popen(command, env=env, cwd=source, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        receipt["launcher_pid"] = process.pid
        base = f"http://127.0.0.1:{args.port}"
        wait_ready(base, process, args.startup_timeout)

        # `xinference-hmr` exec'd into this PID, so the launcher's own cmdline is proof of both
        # the `--hmr-*` strip and the handover to the official console script.
        listener_cmdline = assert_official_argv(process.pid, xinference_argv)

        family = register_family(base, args.model_path) if chat_mode else None
        model_entry = launch_model(base, args.model_path, args.launch_timeout, chat=chat_mode)
        baseline_status, _, baseline_response = infer(base)
        if baseline_status != 200 or not isinstance(baseline_response, dict):
            raise AssertionError(f"baseline {endpoint}: {baseline_status} {baseline_response}")
        baseline_text = response_text(baseline_response)

        records = read_records(record_path)
        load_record = last_record(records, "load_exit")
        if load_record is None:
            raise AssertionError(f"the probe never observed ModelActor.load in a sub-pool process: {records}")
        baseline_request = last_record(records, "request", method=probe_method)
        if baseline_request is None:
            raise AssertionError(f"the probe never observed a {probe_method} request in the actor process")
        baseline_identity = identity(baseline_request)
        if baseline_identity["pid"] != load_record["pid"]:
            raise AssertionError(f"the request ran in a different process than the load: {baseline_identity['pid']} != {load_record['pid']}")
        if load_record.get("load_calls_at_exit", load_record.get("attempt")) != 1:
            raise AssertionError(f"the model was loaded more than once before the edit: {load_record}")
        # The actor process's live module must come from the source root, not from the installed
        # site-packages copy. Without this, an edit could publish "successfully" against a tree
        # the running process never imported -- the concrete hazard described in `runtime_source_resolution`.
        live_file = baseline_request.get("module_file")
        if live_file is None or Path(live_file).resolve() != target.resolve():
            raise AssertionError(f"the actor's live module came from {live_file}, not from the source root target {target}")
        hmr_state = baseline_request.get("hmr_state") or {}
        if not hmr_state.get("installed"):
            raise AssertionError(f"the packaged runtime is not installed in the process that owns the model: {hmr_state}")
        if hmr_state.get("manifest") != manifest:
            raise AssertionError(f"the packaged runtime did not load the exact source manifest: {hmr_state.get('manifest')}")
        if baseline_request.get("co_consts_markers"):
            raise AssertionError(f"the baseline target already carried a marker: {baseline_request}")
        before_log = log_path.read_text(encoding="utf-8", errors="replace")
        load_evidence_before = load_lines(before_log)
        baseline_record_count = len(records)

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

            # Give the watcher time to observe the edit. Nothing is published yet: publication is
            # driven by the actor boundary, so the next real request is what triggers it. That is
            # the difference between this and a watcher that publishes on its own schedule -- and
            # it means the marker must still be absent right now.
            time.sleep(args.watch_settle)
            log_before_request = log_path.read_text(encoding="utf-8", errors="replace")
            if marker_lines(log_before_request, MARKER):
                raise AssertionError("the marker appeared before the post-edit request: the mutation ran without a request boundary")

            post_status, _, post_response = infer(base)
            if post_status != 200 or not isinstance(post_response, dict):
                raise AssertionError(f"post-edit {endpoint}: {post_status} {post_response}")
            post_text = response_text(post_response)

            post_records = read_records(record_path)
            post_request = last_record(post_records, "request", method=probe_method)
            if post_request is None or post_request is baseline_request:
                raise AssertionError("the probe recorded no request after the edit")
            post_identity = identity(post_request)
            if post_identity != baseline_identity:
                raise AssertionError(f"actor/model identity changed across the edit: {baseline_identity} != {post_identity}")
            if len([record for record in post_records if record.get("kind") == "load_exit"]) != 1:
                raise AssertionError(f"the model was loaded again after the edit: {[r for r in post_records if r.get('kind') == 'load_exit']}")

            publications = [event for event in hmr_events(post_records, "published") if event.get("path") == TARGET]
            if not publications:
                raise AssertionError(f"the runtime never published {TARGET}: {list(hmr_events(post_records, 'rejected'))}")
            publication = publications[-1]
            queued = [event for event in hmr_events(post_records, "source_change") if event.get("path") == TARGET and event["t"] <= publication["t"]]
            if not queued:
                raise AssertionError(f"the target was published with no preceding watcher event: {publication}")
            boundary_events = [event for event in hmr_events(post_records, "actor_boundary") if (event.get("sync") or {}).get("published")]
            if not boundary_events:
                raise AssertionError("no actor boundary reported a publication: publication did not happen at the actor's request boundary")

            log_after = log_path.read_text(encoding="utf-8", errors="replace")
            expected_marker = f"{MARKER} pid={baseline_identity['pid']}"
            observed = marker_lines(log_after, MARKER)
            if not any(expected_marker in line for line in observed):
                raise AssertionError(f"marker missing from the sub-pool process that owns the model: expected {expected_marker!r}, saw {observed}")
            if not post_request.get("co_consts_markers"):
                raise AssertionError(f"the actor's live function object does not carry the new marker: {post_request}")
            post_edit_log = log_after[len(before_log) :]
            load_evidence_after_edit = load_lines(post_edit_log)
            if load_evidence_after_edit:
                raise AssertionError(f"model reload evidence appeared after edit: {load_evidence_after_edit}")

            receipt.update(
                {
                    "status": "passed",
                    "model_entry": model_entry,
                    "registered_family": family,
                    "baseline": {"http_status": baseline_status, "text": baseline_text, "usage": baseline_response.get("usage")},
                    "post_edit": {"http_status": post_status, "text": post_text, "usage": post_response.get("usage")},
                    "identity_before": baseline_identity,
                    "identity_after": post_identity,
                    "load_record": load_record,
                    "live_module_file": live_file,
                    "hmr_state_at_baseline": hmr_state,
                    "hmr_state_after_request": post_request.get("hmr_state"),
                    "inserted_line": inserted_line,
                    "mutated_sha256": mutated_hash,
                    "changed_python_sources_while_mutated": changed_while_mutated,
                    "watcher_queued_event": queued[-1],
                    "publication": publication,
                    "actor_boundary_publications": boundary_events,
                    "probe_records_before_edit": baseline_record_count,
                    "probe_records_after_edit": len(post_records),
                    "load_evidence_before_edit": load_evidence_before,
                    "load_evidence_after_edit": load_evidence_after_edit,
                    "marker_log_lines": observed,
                    "listener_cmdline": listener_cmdline,
                    "actor_orig_argv": (hmr_state.get("telemetry") or {}).get("orig_argv"),
                    "assertions": {
                        "hmr_pinned_to_commit": True,
                        "service_started_through_xinference_hmr_console_script": True,
                        "hmr_options_stripped_before_official_xinference_exec": True,
                        "packaged_default_runtime_not_overridden": "--hmr-runtime" not in command,
                        "real_model_launched_into_a_model_actor": True,
                        "baseline_openai_request_200": True,
                        "hmr_installed_in_the_process_that_owns_the_model": True,
                        "live_module_imported_from_source_root": True,
                        "exactly_one_xinference_python_source_changed": True,
                        "exactly_one_unique_print_inserted": True,
                        "target_is_not_rest_or_supervisor": "api/" not in TARGET and "supervisor" not in TARGET,
                        "watcher_queued_target_before_publication": True,
                        "published_at_the_model_actor_request_boundary": True,
                        "marker_absent_until_the_next_real_request": True,
                        "next_real_request_200": True,
                        "marker_from_the_subpool_that_owns_the_model": True,
                        "live_function_object_carries_the_new_marker": True,
                        "same_actor_object_and_class": True,
                        "same_model_object_class_and_parameter_pointers": True,
                        "same_tokenizer_and_batch_scheduler": True,
                        "model_not_relaunched_and_not_loaded_twice": True,
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
        # Teardown happens in `finally`, so the server is still up here: a failed receipt that
        # carries no runtime state is worth almost nothing once the container is gone.
        with contextlib.suppress(Exception):
            receipt["records_at_failure"] = read_records(record_path)[-20:]
        if process is not None and process.poll() is None:
            try:
                _, _, listing = request_json(f"http://127.0.0.1:{args.port}", "GET", "/v1/models", timeout=30)
                receipt["models_at_failure"] = listing
            except Exception as probe_error:
                receipt["state_at_failure_error"] = f"{type(probe_error).__name__}: {probe_error}"
        raise
    finally:
        # Nothing in here may raise before `write_receipt`: this is the only place the receipt is
        # written, and a lost receipt makes the whole run unreportable once the container is gone.
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
            receipt["status"] = "failed"  # a surviving sub-pool invalidates the run, passed assertions or not
        receipt["finished_at"] = time.time()
        write_receipt(receipt_path, receipt)
        # A teardown failure is real, but it must never replace the smoke failure already
        # propagating: that one is why the run is being reported at all.
        if failure is None and teardown_error is not None:
            raise RuntimeError(f"teardown failed: {teardown_error}")

    print(json.dumps({"status": receipt["status"], "receipt_path": str(receipt_path), "log_path": str(log_path)}))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Xinference source root (containing xinference/)")
    parser.add_argument("--results", required=True, help="Directory for receipt and logs")
    parser.add_argument("--model-path", required=True, help="Local directory of the tiny transformers model")
    parser.add_argument("--probe-dir", required=True, help="Directory holding the example's sitecustomize probe")
    parser.add_argument("--probe-record", required=True, help="JSONL file the actor-process probe appends to")
    parser.add_argument("--mode", choices=("completion", "chat"), default="completion", help="Which OpenAI-compatible endpoint this run exercises; one model per run")
    parser.add_argument("--port", type=int, default=29997, help="Xinference REST port")
    parser.add_argument("--startup-timeout", type=float, default=600)
    parser.add_argument("--launch-timeout", type=float, default=900)
    parser.add_argument("--watch-settle", type=float, default=3.0, help="Seconds to let the watcher observe the edit before the next request")
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--xinference-version", required=True)
    parser.add_argument("--xinference-commit", required=True)
    parser.add_argument("--xinference-git-head", required=True)
    parser.add_argument("--installed-source", required=True)
    parser.add_argument("--hmr-core-version", required=True)
    parser.add_argument("--pyth-revision", required=True)
    parser.add_argument("--expected-core-sha256", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
