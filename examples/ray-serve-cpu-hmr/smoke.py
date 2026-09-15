"""Full smoke: real model, real OpenAI-compatible requests, HMR at a request boundary.

Runs inside the official Ray CPU image, in the same container as the Serve cluster. Every
assertion is on evidence read back from the live replica, not on this script's own bookkeeping.

Sequence:
  0. Assert the `hmr` under test came from the pinned source revision, not from an index: its
     dist has a PEP 610 `direct_url.json` naming the extracted pinned tree, and the bytes of
     `reactivity/hmr/core.py` hash to a value computed on the host, outside this container,
     from that revision's own tarball.
  1. Serve the app; assert a real completion and a real chat completion (200, non-echo text).
  2. Record the replica's PID and the model/class/parameter identities.
  3. Mutate `Replica._unpack_proxy_args` in `ray/serve/_private/replica.py` -- a function that
     every real HTTP request to this replica passes through -- inserting a unique print.
  4. Assert the marker appears in the REPLICA's log on the next real request, that the PID and
     all model identities are unchanged, and that no second model load happened.
  5. Assert a syntax error and then a runtime error both leave the old implementation serving.
  6. Restore the source exactly and verify.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import ray
from ray import serve

BASE = "http://127.0.0.1:8000"
ANCHOR = '        ray_trace_ctx = request_kwargs.pop("_ray_trace_ctx", None)\n'
MARKER = f"HMR_MARKER_{uuid.uuid4().hex[:12]}"
RUNTIME_MARKER = f"HMR_RUNTIME_MARKER_{uuid.uuid4().hex[:8]}"
RECEIPT = Path(os.getenv("HMR_RECEIPT", "/out/smoke-receipt.json"))

PINNED_SHA = os.getenv("HMR_PYTH_ON_LINE_SHA", "")
EXPECTED_CORE_SHA256 = os.getenv("HMR_EXPECTED_CORE_SHA256", "")
EXPECTED_HMR_VERSION = os.getenv("HMR_EXPECTED_HMR_VERSION", "")

results: list[dict[str, Any]] = []
failures: list[str] = []


def check(name: str, ok: bool, **detail: Any) -> bool:  # noqa: FBT001 -- an assertion helper; a keyword here would only obscure the call sites
    results.append({"check": name, "ok": bool(ok), **detail})
    print(f"{'PASS' if ok else 'FAIL'} {name} {json.dumps(detail, default=str)}", flush=True)
    if not ok:
        failures.append(name)
    return bool(ok)


def replica_stdout_paths() -> list[Path]:
    """The replica actor's own stdout files.

    Ray routes a deployment's plain `print` to the actor worker's `.out`, not to
    `logs/serve/replica_*.log` (which only receives the Serve logger's records). The worker
    `.out` of the process that loaded the model IS the replica log for this purpose, and tying
    the marker to that same file is what proves the mutated code ran in the model-holding actor.
    """
    root = Path("/tmp/ray/session_latest/logs")
    return sorted(path for path in root.glob("worker-*.out") if path.is_file()) if root.is_dir() else []


def replica_logs() -> str:
    return "".join(path.read_text(errors="replace") for path in replica_stdout_paths())


def model_holder_log() -> str:
    """Only the log of the process that printed MODEL_LOADED, i.e. the one holding the model."""
    return "".join(text for path in replica_stdout_paths() if "MODEL_LOADED pid=" in (text := path.read_text(errors="replace")))


def replica_pids_from_logs() -> set[int]:
    return {int(match) for match in re.findall(r"MODEL_LOADED pid=(\d+)", replica_logs())}


def model_load_count() -> int:
    return len(re.findall(r"MODEL_LOADED pid=", replica_logs()))


def hmr_provenance() -> dict[str, Any]:
    """Where the installed `hmr` actually came from, read off the dist rather than assumed.

    A PyPI install has no `direct_url.json` at all, so its presence -- with a `dir_info` URL
    under the extracted pinned tree -- is what separates "installed from the pinned source" from
    "resolved from an index". The version is `core.py`'s `__version__` (pdm-backend reads it from
    that file), so the pinned revision fixes it too and the two are cross-checkable.
    """
    from reactivity.hmr import core

    dist = importlib.metadata.distribution("hmr")
    raw = dist.read_text("direct_url.json")
    direct = json.loads(raw) if raw else None
    assert core.__file__ is not None
    core_path = Path(core.__file__).resolve()
    return {
        "version": dist.version,
        "installer": (dist.read_text("INSTALLER") or "").strip(),
        "direct_url": direct,
        "core_path": str(core_path),
        "core_sha256": hashlib.sha256(core_path.read_bytes()).hexdigest(),
        "core_version_attr": getattr(core, "__version__", None),
    }


def post_completion(prompt: str = "Hello", max_tokens: int = 8) -> httpx.Response:
    return httpx.post(f"{BASE}/v1/completions", json={"prompt": prompt, "max_tokens": max_tokens}, timeout=120)


def post_chat(content: str = "Hello", max_tokens: int = 8) -> httpx.Response:
    return httpx.post(f"{BASE}/v1/chat/completions", json={"messages": [{"role": "user", "content": content}], "max_tokens": max_tokens}, timeout=120)


def wait_for_marker(marker: str, timeout: float = 60.0) -> bool:
    """Wait for the marker in the model holder's own log, which may lag the response by a flush."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker in model_holder_log():
            return True
        time.sleep(0.25)
    return False


def main() -> int:
    import app as example_app

    # `ray.serve._private` is the module under test, not an internal being reached around.
    replica_source = Path(ray.serve._private.replica.__file__).resolve()  # noqa: SLF001
    original = replica_source.read_text(encoding="utf-8")
    original_digest = subprocess.run(["sha256sum", str(replica_source)], capture_output=True, text=True).stdout.split()[0]

    # ---- 0. the `hmr` under test is the pinned source, not an index resolution --------------
    provenance = hmr_provenance()
    direct = provenance["direct_url"] or {}
    direct_url = str(direct.get("url", ""))
    check(
        "hmr_installed_from_direct_source",
        bool(direct) and direct.get("dir_info", {}).get("editable") is not True and direct_url.startswith("file://"),
        direct_url=direct_url or None,
        installer=provenance["installer"],
    )
    check("hmr_source_path_carries_pinned_sha", bool(PINNED_SHA) and PINNED_SHA in direct_url, pinned_sha=PINNED_SHA, url=direct_url or None)
    check(
        "hmr_core_sha256_matches_expected",
        bool(EXPECTED_CORE_SHA256) and provenance["core_sha256"] == EXPECTED_CORE_SHA256,
        expected=EXPECTED_CORE_SHA256 or None,
        actual=provenance["core_sha256"],
    )
    check(
        "hmr_version_matches_pinned_source",
        bool(EXPECTED_HMR_VERSION) and provenance["version"] == EXPECTED_HMR_VERSION == provenance["core_version_attr"],
        expected=EXPECTED_HMR_VERSION or None,
        dist=provenance["version"],
        core=provenance["core_version_attr"],
    )

    ray.init(num_cpus=4, include_dashboard=False, log_to_driver=False)
    serve.run(example_app.app, route_prefix="/")

    try:
        # ---- 1. real model, real OpenAI-compatible responses -------------------------------
        completion = post_completion("The capital of France is")
        check("completion_http_200", completion.status_code == 200, status=completion.status_code)
        body = completion.json()
        text = body["choices"][0]["text"]
        check("completion_shape_openai", body.get("object") == "text_completion" and isinstance(text, str), object=body.get("object"))
        check("completion_not_echo", "The capital of France is" not in text, text=text[:120])

        chat = post_chat("Say something")
        check("chat_http_200", chat.status_code == 200, status=chat.status_code)
        chat_body = chat.json()
        chat_text = chat_body["choices"][0]["message"]["content"]
        check("chat_shape_openai", chat_body.get("object") == "chat.completion" and isinstance(chat_text, str), object=chat_body.get("object"))
        check("chat_not_echo", "Say something" not in chat_text, text=chat_text[:120])

        models = httpx.get(f"{BASE}/v1/models", timeout=30)
        check("models_http_200", models.status_code == 200 and models.json()["data"][0]["id"] == os.getenv("HMR_MODEL_ID", "sshleifer/tiny-gpt2"), status=models.status_code)

        # ---- 2. baseline identities --------------------------------------------------------
        before = body["x_identity"]
        hmr_state_before = httpx.get(f"{BASE}/hmr/state", timeout=30).json()
        check("hmr_healthy", hmr_state_before.get("healthy") is True, watching=hmr_state_before.get("watching"), error=hmr_state_before.get("watcher_error"))
        check("hmr_pid_is_replica_pid", hmr_state_before.get("pid") == before["pid"], hmr_pid=hmr_state_before.get("pid"), replica_pid=before["pid"])
        check("hmr_origin_is_watched_file", hmr_state_before.get("origin") == str(replica_source), origin=hmr_state_before.get("origin"))
        check("one_model_load_before", model_load_count() == 1, loads=model_load_count())
        check("marker_absent_before", MARKER not in replica_logs())

        # ---- 3. mutate a function every real request passes through ------------------------
        assert ANCHOR in original, "anchor not found in replica.py"
        mutated = original.replace(ANCHOR, f'        print("{MARKER}", flush=True)\n{ANCHOR}', 1)
        check("mutation_changes_source", mutated != original)
        replica_source.write_text(mutated, encoding="utf-8")

        published = False
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = httpx.get(f"{BASE}/hmr/state", timeout=30).json()
            if any(item["event"] == "published" for item in state.get("events", [])):
                published = True
                break
            time.sleep(0.5)
        state_after = httpx.get(f"{BASE}/hmr/state", timeout=30).json()
        events = state_after.get("events", [])
        check("hmr_published", published, events=[item["event"] for item in events])
        publication = next((item for item in events if item["event"] == "published"), {})
        check("replica_class_rebound", publication.get("replica_class_is_new") is True and publication.get("replica_class_changed") is True, detail=publication.get("replica_class_changed"))
        check("wrapper_class_rebound", publication.get("wrapper_class_is_new") is True and publication.get("wrapper_class_changed") is True)
        check(
            "user_callable_identity_preserved",
            publication.get("user_callable_id_before") == publication.get("user_callable_id_after"),
            before=publication.get("user_callable_id_before"),
            after=publication.get("user_callable_id_after"),
        )
        check(
            "cached_bound_methods_dropped",
            isinstance(publication.get("cached_methods_dropped"), list) and len(publication["cached_methods_dropped"]) > 0,
            dropped=publication.get("cached_methods_dropped"),
        )
        check("published_by_replica_pid", publication.get("pid") == before["pid"], publish_pid=publication.get("pid"), replica_pid=before["pid"])

        # ---- 4. next real request executes the new code ------------------------------------
        after_completion = post_completion("After the reload")
        check("post_reload_completion_200", after_completion.status_code == 200, status=after_completion.status_code)
        after = after_completion.json()["x_identity"]
        check("marker_in_replica_log", wait_for_marker(MARKER), pids_in_log=sorted(replica_pids_from_logs()))
        check("same_replica_pid", after["pid"] == before["pid"], before=before["pid"], after=after["pid"])
        check("same_model_object", after["model_object_id"] == before["model_object_id"], before=before["model_object_id"], after=after["model_object_id"])
        check("same_model_class", after["model_class_id"] == before["model_class_id"])
        check("same_first_parameter", after["first_parameter_id"] == before["first_parameter_id"] and after["first_parameter_data_ptr"] == before["first_parameter_data_ptr"])
        check("same_tokenizer_object", after["tokenizer_object_id"] == before["tokenizer_object_id"])
        check("same_deployment_instance", after["instance_id"] == before["instance_id"])
        check("no_second_model_load", model_load_count() == 1 and after["load_count"] == 1 and after["loaded_at"] == before["loaded_at"], loads=model_load_count())
        after_text = after_completion.json()["choices"][0]["text"]
        check("post_reload_still_real_generation", isinstance(after_text, str) and "After the reload" not in after_text, text=after_text[:120])

        # ---- 4b. an in-flight request defers publication, and still completes ---------------
        # A real generation long enough to still be inside user code when the edit lands. The
        # publication must wait for it rather than swapping classes underneath it.
        second_marker = f"HMR_SECOND_{uuid.uuid4().hex[:8]}"
        deferred_mutation = original.replace(ANCHOR, f'        print("{MARKER}", flush=True)\n        print("{second_marker}", flush=True)\n{ANCHOR}', 1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            slow = pool.submit(post_completion, "A slow one", 320)
            time.sleep(0.4)  # let it get past dispatch and into `model.generate`
            replica_source.write_text(deferred_mutation, encoding="utf-8")
            slow_response = slow.result()
        check("in_flight_request_completed", slow_response.status_code == 200, status=slow_response.status_code)
        check("in_flight_identity_unchanged", slow_response.json()["x_identity"]["model_object_id"] == before["model_object_id"])

        second_published = False
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            defer_state = httpx.get(f"{BASE}/hmr/state", timeout=30).json()
            if len([item for item in defer_state.get("events", []) if item["event"] == "published"]) >= 2:
                second_published = True
                break
            time.sleep(0.5)
        defer_events = httpx.get(f"{BASE}/hmr/state", timeout=30).json().get("events", [])
        check("second_publication", second_published, events=[item["event"] for item in defer_events])
        check(
            "publication_deferred_while_in_flight",
            any(item["event"] == "deferred" and item.get("ongoing_requests", 0) > 0 for item in defer_events),
            deferred=[item for item in defer_events if item["event"] == "deferred"],
        )
        post_defer = post_completion("After the deferred publication")
        check("post_defer_completion_200", post_defer.status_code == 200, status=post_defer.status_code)
        check("second_marker_in_replica_log", wait_for_marker(second_marker))
        check("post_defer_same_model_object", post_defer.json()["x_identity"]["model_object_id"] == before["model_object_id"])
        check("post_defer_no_new_load", model_load_count() == 1, loads=model_load_count())
        mutated = deferred_mutation

        # ---- 5. failure keeps the old implementation alive ---------------------------------
        marker_count_before_failures = model_holder_log().count(MARKER)
        replica_source.write_text(mutated + "\nthis is not valid python(\n", encoding="utf-8")
        time.sleep(3)
        syntax_state = httpx.get(f"{BASE}/hmr/state", timeout=30).json()
        rejections = [item for item in syntax_state.get("events", []) if item["event"] == "rejected"]
        check("syntax_error_rejected", any(item.get("phase") == "syntax_preflight" for item in rejections), phases=[item.get("phase") for item in rejections])
        survivor = post_completion("Survives a syntax error")
        check("survives_syntax_error", survivor.status_code == 200, status=survivor.status_code)
        # The previously published implementation -- the one with the marker -- must still be the
        # one running, so the count has to keep growing while the file on disk is unparseable.
        marker_kept_running = False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if model_holder_log().count(MARKER) > marker_count_before_failures:
                marker_kept_running = True
                break
            time.sleep(0.25)
        check("old_impl_still_serving_after_syntax_error", marker_kept_running, before=marker_count_before_failures, after=model_holder_log().count(MARKER))

        runtime_broken = mutated.replace(ANCHOR, f'        raise RuntimeError("{RUNTIME_MARKER}")\n{ANCHOR}', 1)
        replica_source.write_text(runtime_broken + '\nraise RuntimeError("' + RUNTIME_MARKER + '")\n', encoding="utf-8")
        runtime_rejected = False
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            runtime_state = httpx.get(f"{BASE}/hmr/state", timeout=30).json()
            if any(item["event"] == "rejected" and item.get("phase") == "reload" for item in runtime_state.get("events", [])):
                runtime_rejected = True
                break
            time.sleep(0.5)
        check("runtime_error_rejected", runtime_rejected)
        survivor2 = post_completion("Survives a runtime error")
        check("survives_runtime_error", survivor2.status_code == 200, status=survivor2.status_code)
        check("runtime_error_not_raised_to_client", RUNTIME_MARKER not in survivor2.text)

        # ---- 6. restore ---------------------------------------------------------------------
        replica_source.write_text(original, encoding="utf-8")
        restored_digest = subprocess.run(["sha256sum", str(replica_source)], capture_output=True, text=True).stdout.split()[0]
        check("source_restored_exactly", restored_digest == original_digest, before=original_digest, after=restored_digest)
        final = post_completion("After restore")
        check("serving_after_restore", final.status_code == 200, status=final.status_code)
    finally:
        if replica_source.read_text(encoding="utf-8") != original:
            replica_source.write_text(original, encoding="utf-8")
        try:
            serve.shutdown()
        finally:
            ray.shutdown()

    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(
        json.dumps(
            {
                "run_id": os.getenv("HMR_RUN_ID"),
                "marker": MARKER,
                "ray_version": ray.__version__,
                "python": sys.version,
                "model_id": example_app.MODEL_ID,
                "image": {
                    "base": os.getenv("HMR_BASE_IMAGE"),
                    "base_id": os.getenv("HMR_BASE_IMAGE_ID"),
                    "base_digest": os.getenv("HMR_BASE_IMAGE_DIGEST"),
                    "derived": os.getenv("HMR_DERIVED_IMAGE"),
                    "derived_id": os.getenv("HMR_DERIVED_IMAGE_ID"),
                },
                "hmr_source": {
                    "pinned_revision": PINNED_SHA or None,
                    "pinned_tarball_sha256": os.getenv("HMR_PINNED_TARBALL_SHA256"),
                    "expected_core_sha256": EXPECTED_CORE_SHA256 or None,
                    **provenance,
                },
                "replica_source": str(replica_source),
                "replica_source_sha256": original_digest,
                "checks": results,
                "failures": failures,
                "ok": not failures,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nSMOKE {'PASS' if not failures else 'FAIL'} {len(results) - len(failures)}/{len(results)} checks", flush=True)
    if failures:
        print("failed: " + ", ".join(failures), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
