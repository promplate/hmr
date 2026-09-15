"""Real modern-BentoML acceptance; drives the package runtime and never reimplements HMR.

This is a test driver, not server code: it deliberately blocks on the subprocess it supervises and
on the log files it reads, so the flake8-async rules about blocking calls do not apply here.
"""
# ruff: noqa: ASYNC109, ASYNC220, ASYNC221, ASYNC222, ASYNC240

import argparse
import ast
import asyncio
import ctypes
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import traceback
import uuid
from contextlib import suppress
from importlib import metadata
from pathlib import Path

import httpx
from bentoml_hmr.scope import CLASS, FUNCTION, TARGET, build_manifest, write_manifest

EXAMPLE = Path(__file__).resolve().parent
HEAD = "517b343b81aeb0b01bbd908e58e53ad9c12ef7eb"
MODEL_ID = "sshleifer/tiny-gpt2"
# The one HMR build this spike is allowed to run against, and the file whose bytes decide it.
HMR_REPO = "https://github.com/promplate/pyth-on-line"
HMR_REVISION = "d410f975367e8a29b17183d108ef09a089e42b63"
HMR_SUBDIRECTORY = "packages/hmr"
CORE_RELATIVE = "reactivity/hmr/core.py"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sources(root):
    return {str(path.relative_to(root)): digest(path.read_bytes()) for path in root.rglob("*.py")}


def records(log, prefix):
    return [json.loads(line[len(prefix) :]) for line in log.read_text(errors="replace").splitlines() if line.startswith(prefix)]


def provenance(core_path, checkout):
    """Pin the running HMR to `HMR_REVISION` from two sides that cannot agree by accident.

    `direct_url.json` says which revision the installer resolved, so a floating PyPI wheel (which has
    no such file) fails here rather than passing as "hmr 0.7.6.2" — the PyPI and commit builds of this
    spike's pin share a version string but not their bytes.

    The expected hash comes from `git cat-file` against a checkout of that revision, i.e. from the
    object database rather than from the installed tree: hashing the same file twice would confirm
    only that the file equals itself. Every `.py` under `reactivity/` is compared, so the pin covers
    the whole package and not just the one file named in the receipt.
    """
    core = Path(core_path).resolve()
    site_packages = core.parents[2]
    urls = sorted(site_packages.glob("hmr-*.dist-info/direct_url.json"))
    if not urls:
        distributions = sorted(path.name for path in site_packages.glob("hmr-*.dist-info"))
        raise RuntimeError(f"no hmr direct_url.json under {site_packages}: {distributions or 'no hmr distribution at all'}; hmr must be installed from {HMR_REPO}@{HMR_REVISION}, not from PyPI")
    if len(urls) > 1:
        raise RuntimeError(f"more than one hmr distribution under {site_packages}: {[str(path) for path in urls]}")
    direct_url = json.loads(urls[0].read_text(encoding="utf-8"))
    revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    if revision != HMR_REVISION:
        raise RuntimeError(f"--hmr-checkout {checkout} is at {revision}, expected {HMR_REVISION}")
    listing = subprocess.check_output(["git", "-C", str(checkout), "ls-tree", "-r", "--name-only", HMR_REVISION, f"{HMR_SUBDIRECTORY}/reactivity"], text=True)
    tracked = sorted(name for name in listing.splitlines() if name.endswith(".py"))
    if f"{HMR_SUBDIRECTORY}/{CORE_RELATIVE}" not in tracked:
        raise RuntimeError(f"{CORE_RELATIVE} is not tracked at {HMR_REVISION} under {HMR_SUBDIRECTORY}")
    expected, mismatched = {}, []
    for name in tracked:
        relative = name[len(HMR_SUBDIRECTORY) + 1 :]
        blob = subprocess.check_output(["git", "-C", str(checkout), "cat-file", "blob", f"{HMR_REVISION}:{name}"])
        expected[relative] = digest(blob)
        installed = site_packages / relative
        if not installed.is_file() or digest(installed.read_bytes()) != expected[relative]:
            mismatched.append(relative)
    return {
        "direct_url": direct_url,
        "expected_source": {"repo": HMR_REPO, "revision": HMR_REVISION, "subdirectory": HMR_SUBDIRECTORY, "checkout": str(checkout), "checkout_head": revision},
        "core_relative": CORE_RELATIVE,
        "core_sha256_installed": digest(core.read_bytes()),
        "core_sha256_expected": expected[CORE_RELATIVE],
        "core_blob_oid": subprocess.check_output(["git", "-C", str(checkout), "rev-parse", f"{HMR_REVISION}:{HMR_SUBDIRECTORY}/{CORE_RELATIVE}"], text=True).strip(),
        "compared_files": len(expected),
        "mismatched_files": mismatched,
    }


async def wait_for(predicate, process, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited: {process.returncode}")
        if result := predicate():
            return result
        await asyncio.sleep(0.05)
    raise TimeoutError("evidence wait timed out")


async def stop(process):
    for sig, seconds in [(signal.SIGTERM, 10), (signal.SIGKILL, 5)]:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, sig)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            process.poll()
            with suppress(ChildProcessError):
                while os.waitpid(-process.pid, os.WNOHANG)[0]:
                    pass
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return True
            await asyncio.sleep(0.1)
    return False


def mutation(original, statement):
    """Insert one statement at the top of the published function's body, by AST line number.

    The signature line is shared with two other `deserialize_model` definitions in this file, so a
    textual anchor would be ambiguous; the class/function pair is resolved through the AST instead.
    """
    tree = ast.parse(original)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == CLASS)
    function = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == FUNCTION)
    lines = original.splitlines(keepends=True)
    lines.insert(function.body[0].lineno - 1, f"        {statement}\n".encode())
    return b"".join(lines)


async def run(args):
    from reactivity.hmr import core

    results = args.results.resolve()
    results.mkdir(parents=True, exist_ok=False)
    full_log = results / "full.log"
    source = args.source.resolve()
    target = source / TARGET
    original = target.read_bytes()
    before = sources(source)
    manifest = write_manifest(build_manifest(source), results / "manifest.json")
    token = "BENTOML_HMR_" + uuid.uuid4().hex
    marker_statement = f'print({token!r}, "pid", __import__("os").getpid(), "cls", cls.__name__, flush=True)'
    candidate = mutation(original, marker_statement)
    ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER, so orphaned circus children are still reapable
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = [sys.executable, "-u", "-m", "bentoml_hmr", "--manifest", str(manifest), "--port", str(port), "app:TinyGPT2"]
    if args.preflight:
        command = [sys.executable, "-u", "-m", "bentoml", "serve", "app:TinyGPT2", "--port", str(port)]
    env = dict(os.environ, BENTOML_PROBE_RESULTS=str(results), HF_HUB_OFFLINE="1", TOKENIZERS_PARALLELISM="false", PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="2", COLUMNS="200")
    receipt = {
        "command": command,
        "mode": "preflight" if args.preflight else "smoke",
        "source_head": subprocess.check_output(["git", "-C", str(source.parent), "rev-parse", "HEAD"], text=True).strip(),
        "source_root": str(source),
        "core_path": core.__file__,
        "core_sha256": digest(Path(core.__file__).read_bytes()),
        "hmr_provenance": provenance(core.__file__, args.hmr_checkout.resolve()),
        # `circus` has no distribution of its own here; BentoML vendors it through `kantoku`.
        "versions": {name: metadata.version(name) for name in ("bentoml", "hmr", "watchfiles", "torch", "transformers", "kantoku", "uvicorn")},
        "model_id": MODEL_ID,
        "marker": token,
        "assertions": {},
        "responses": [],
        "success": False,
    }
    process = None
    held = None
    release = results / "release"
    checks = receipt["assertions"]
    try:
        assert receipt["source_head"] == HEAD, receipt["source_head"]
        pin = receipt["hmr_provenance"]
        assert pin["direct_url"]["vcs_info"]["commit_id"] == HMR_REVISION, pin["direct_url"]
        assert pin["direct_url"]["vcs_info"]["requested_revision"] == HMR_REVISION, pin["direct_url"]
        assert pin["direct_url"]["url"] == HMR_REPO and pin["direct_url"]["subdirectory"] == HMR_SUBDIRECTORY, pin["direct_url"]
        assert pin["core_sha256_installed"] == pin["core_sha256_expected"], pin
        assert receipt["core_sha256"] == pin["core_sha256_expected"], receipt["core_sha256"]
        assert not pin["mismatched_files"] and pin["compared_files"] >= 20, pin
        checks["hmr_pinned_to_commit"] = True
        with full_log.open("w") as output:
            process = subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT, cwd=EXAMPLE, start_new_session=True)
        receipt["parent_pid"] = process.pid
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=120) as client:
            for _ in range(180):
                if process.poll() is not None:
                    raise RuntimeError(f"server exited: {process.returncode}")
                with suppress(httpx.HTTPError):
                    if (await client.get("/readyz", timeout=2)).status_code == 200:
                        break
                await asyncio.sleep(1)
            else:
                raise TimeoutError("readyz readiness")

            async def request(label, prompt="Hello there"):
                body = {"messages": [{"role": "user", "content": prompt}], "model": MODEL_ID, "max_tokens": 4}
                response = await client.post("/v1/chat/completions", json=body)
                receipt["responses"].append({"label": label, "status": response.status_code, "body": response.text[:600]})
                assert response.status_code == 200, (label, response.status_code, response.text[:400])
                payload = response.json()
                # A real generation, not an echo: the model appends tokens the prompt does not contain.
                content = payload["choices"][0]["message"]["content"]
                assert content.startswith(prompt) and len(content) > len(prompt) and payload["usage"]["completion_tokens"] == 4, (label, payload)
                return payload

            await request("baseline")
            checks["baseline_200_real_generation"] = True
            probes = records(full_log, "BENTOML_PROBE ")
            setup = [row for row in probes if row["kind"] == "setup_done"]
            assert len(setup) == 1, probes
            worker_pid = setup[0]["pid"]
            receipt["worker_pid"] = worker_pid
            if not args.preflight:
                events = lambda: records(full_log, "BENTOML_HMR ")  # noqa: E731
                installed = [row for row in events() if row["kind"] == "worker_installed"]
                assert len(installed) == 1 and installed[0]["pid"] == worker_pid and installed[0]["watcher_alive"], installed
                assert installed[0]["reactive_module"] == str(target), installed
                assert {row["pid"] for row in events()} == {worker_pid}
                checks["worker_only_injection"] = True
                receipt["topology"] = []
                for proc in Path("/proc").iterdir():
                    if proc.name.isdigit():
                        with suppress(ProcessLookupError, FileNotFoundError, PermissionError):
                            if os.getpgid(int(proc.name)) == process.pid:
                                receipt["topology"].append({"pid": int(proc.name), "cmdline": (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode()[:300]})

                async def write_and_queue(data):
                    target.write_bytes(data)
                    expected = digest(data)
                    await wait_for(lambda: any(row["kind"] == "source_change" and row["sha256"] == expected for row in events()), process)
                    return expected

                held = asyncio.create_task(request("inflight_old", "HMR_HOLD"))
                await wait_for(lambda: any(row["kind"] == "hold_begin" for row in records(full_log, "BENTOML_PROBE ")), process)
                candidate_digest = await write_and_queue(candidate)
                assert not any(row["kind"] == "published" for row in events())
                assert any(row["kind"] == "publication_deferred" for row in events())
                assert token not in full_log.read_text(errors="replace")
                checks["inflight_deferred"] = True
                release.touch()
                with suppress(AssertionError):  # the held request runs old code; its own body assertion is not the subject here
                    await held
                held = None
                assert not any(row["kind"] == "published" for row in events())
                await request("after_edit")
                publication = next(row for row in events() if row["kind"] == "published" and row["sha256"] == candidate_digest)
                assert publication["pid"] == worker_pid and not publication["active"], publication
                assert publication["previous_function_id"] != publication["function_id"], publication
                assert f"{token} pid {worker_pid} cls" in full_log.read_text(errors="replace")
                checks["worker_publication_and_marker"] = True

                for label, statement in [("syntax", "if :"), ("runtime", "raise RuntimeError('candidate runtime failure')")]:
                    bad_digest = await write_and_queue(mutation(original, statement) if label == "runtime" else original.replace(b"class JSONSerde", b"class JSONSerde("))
                    marker_count = full_log.read_text(errors="replace").count(f"{token} pid")
                    await request(label + "_old_survives")
                    await request(label + "_retry_old_survives")
                    rejected = [row for row in events() if row["kind"] == "rejected" and row["sha256"] == bad_digest]
                    assert len(rejected) >= 1 and all(row["retryable"] for row in rejected), rejected
                    # The old implementation is still the live one, so its marker keeps printing.
                    assert full_log.read_text(errors="replace").count(f"{token} pid") >= marker_count + 2
                    checks[label + "_rollback_retry"] = True
                    marker_statement += " "
                    candidate = mutation(original, marker_statement)
                    retry_digest = await write_and_queue(candidate)
                    await request(label + "_fixed")
                    assert any(row["kind"] == "published" and row["sha256"] == retry_digest for row in events())
                    checks[label + "_recovers"] = True
                changed = [name for name in before if sources(source)[name] != before[name]]
                checks["one_changed_source"] = changed == [TARGET]
                assert checks["one_changed_source"], changed
                await write_and_queue(original)
                marker_count = full_log.read_text(errors="replace").count(f"{token} pid")
                await request("restored")
                assert full_log.read_text(errors="replace").count(f"{token} pid") == marker_count
                checks["restored_implementation"] = True
                receipt["events"] = events()
            probes = records(full_log, "BENTOML_PROBE ")
            key = lambda row: {name: row[name] for name in ("pid", "model_id", "model_class", "model_class_id", "service_instance_id", "parameters")}  # noqa: E731
            assert all(key(row) == key(setup[0]) for row in probes if "model_id" in row and row["kind"] != "weight_load_start")
            assert len([row for row in probes if row["kind"] == "setup_done"]) == len([row for row in probes if row["kind"] == "weight_load_start"]) == 1
            checks["identity_continuous"] = checks["single_setup_and_weight_load"] = True
            receipt["identity"] = key(setup[0])
            receipt["probes"] = probes
        receipt["success"] = True
    except BaseException:
        receipt["error"] = traceback.format_exc()
    finally:
        target.write_bytes(original)
        release.touch()
        if held is not None:
            with suppress(BaseException):
                await held
        checks["process_group_gone"] = await stop(process) if process else True
        receipt["server_exit"] = process.returncode if process else None
        with socket.socket() as sock:
            checks["port_closed"] = sock.connect_ex(("127.0.0.1", port)) != 0
        checks["source_bytes_restored"] = target.read_bytes() == original and before == sources(source)
        release.unlink(missing_ok=True)
        receipt["success"] = receipt["success"] and all(checks.values())
        receipt["exit"] = 0 if receipt["success"] else 1
        (results / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"results": str(results), "exit": receipt["exit"], "assertions": checks, "error": receipt.get("error")}, indent=2))
    return receipt["exit"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--hmr-checkout", type=Path, required=True, help=f"a checkout of {HMR_REPO} at {HMR_REVISION}, outside the venv, used to compute the expected hashes")
    parser.add_argument("--preflight", action="store_true")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
