"""What the smoke can be held to without a GPU, a model, or the official image.

Everything here exercises the real functions `smoke.run` calls. The parts that need a
live server (`wait_ready`, `wait_published`) are driven through their injected
dependencies, not reimplemented.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from sglang_hmr.runtime.scope import DEPENDENT, DEPENDENT_PATH, TARGET
from smoke import (
    build_hmr_argv,
    build_manifest,
    build_sglang_argv,
    identity,
    image_metadata,
    load_lines,
    preflight,
    published_record,
    queued_before,
    rejected_records,
    resolve_paths,
    teardown,
    wait_published,
    wait_ready,
    write_receipt,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_SUPPORTS_SIGKILL = hasattr(signal, "SIGKILL")


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    for relative in (TARGET, DEPENDENT_PATH):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    return root


def snapshot(*events: dict[str, Any]) -> dict[str, Any]:
    return {"pending": [], "installed": True, "telemetry": {"events": list(events)}}


# --- results path absolutization and runner preflight ---


def test_resolve_paths_absolutizes_both_relative_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A relative `--results` would put the receipt somewhere that depends on the CWD, and
    the manifest's `source_root` would then not equal the runtime's resolved root."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "s").mkdir()
    source, results = resolve_paths("s", "out/results")
    assert source.is_absolute() and results.is_absolute()
    assert source == (tmp_path / "s").resolve()
    assert results == (tmp_path / "out" / "results").resolve()


def test_resolve_paths_normalizes_dot_segments(tmp_path: Path):
    source, _ = resolve_paths(str(tmp_path / "a" / ".." / "b"), str(tmp_path))
    assert source == (tmp_path / "b").resolve()
    assert ".." not in source.parts


def test_preflight_rejects_a_source_root_without_the_target(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="runtime target absent"):
        preflight(tmp_path)


def test_preflight_rejects_a_missing_console_script(source_root: Path, monkeypatch: pytest.MonkeyPatch):
    """Without the wrapper on PATH the smoke would silently prove nothing about `sglang-hmr`."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(FileNotFoundError, match="`sglang-hmr` console script is not installed"):
        preflight(source_root)


def test_preflight_returns_the_target_and_the_launcher(source_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sglang-hmr" if name == "sglang-hmr" else None)
    target, launcher = preflight(source_root)
    assert target == source_root / TARGET
    assert launcher == "/usr/bin/sglang-hmr"


# --- manifest and command construction ---


def test_build_manifest_states_the_two_file_scope_the_runtime_accepts(source_root: Path):
    manifest = build_manifest(source_root)
    assert manifest["schema_version"] == 1
    assert manifest["source_root"] == str(source_root)
    assert manifest["reactive_paths"] == [TARGET, DEPENDENT_PATH]
    assert manifest["auto_paths"] == [TARGET]  # only the provider auto-publishes
    assert manifest["forced_dependents"] == {TARGET: [DEPENDENT]}
    assert [item["path"] for item in manifest["files"]] == [TARGET, DEPENDENT_PATH]


def test_build_manifest_is_accepted_by_the_packaged_runtime(source_root: Path, tmp_path: Path):
    """The smoke builds its manifest by hand, so it must still satisfy the real loader."""
    from sglang_hmr.runtime.scope import load_manifest

    path = tmp_path / "m.json"
    path.write_text(json.dumps(build_manifest(source_root)), encoding="utf-8")
    assert load_manifest(path, source_root).source_root == source_root


def test_sglang_argv_is_official_only_and_starts_with_serve():
    argv = build_sglang_argv("facebook/opt-125m", 31000)
    assert argv[0] == "serve"
    assert not any(token.startswith("--hmr-") for token in argv)
    assert "--port" in argv and argv[argv.index("--port") + 1] == "31000"
    assert argv[argv.index("--device") + 1] == "cpu"


def test_hmr_argv_carries_only_hmr_options(tmp_path: Path):
    argv = build_hmr_argv(tmp_path, tmp_path / "m.json")
    assert argv == ["--hmr-source-root", str(tmp_path), "--hmr-manifest", str(tmp_path / "m.json")]
    assert "--hmr-runtime" not in argv  # the packaged default must not be overridden


def test_command_keeps_hmr_options_ahead_of_the_official_argv(tmp_path: Path):
    """`sglang-hmr` splits at the first positional, so every `--hmr-*` must precede `serve`."""
    command = ["/usr/bin/sglang-hmr", *build_hmr_argv(tmp_path, tmp_path / "m.json"), *build_sglang_argv("m", 31000)]
    assert command.index("serve") > max(index for index, token in enumerate(command) if token.startswith("--hmr-"))


def test_the_wrapper_strips_exactly_what_the_smoke_passes_as_hmr_argv(source_root: Path, tmp_path: Path):
    """The receipt claims the `--hmr-*` options never reach `sglang`; check that against the real splitter."""
    from sglang_hmr import split_argv

    manifest_path = tmp_path / "m.json"
    manifest_path.write_text(json.dumps(build_manifest(source_root)), encoding="utf-8")
    sglang_argv = build_sglang_argv("facebook/opt-125m", 31000)
    options, print_env, forwarded = split_argv([*build_hmr_argv(source_root, manifest_path), *sglang_argv])
    assert forwarded == sglang_argv
    assert options == {"HMR_SGLANG_SOURCE_ROOT": str(source_root), "HMR_SGLANG_MANIFEST": str(manifest_path)}
    assert not print_env


# --- official image metadata ---


def metadata_args(**overrides: object) -> argparse.Namespace:
    base = {
        "image": "lmsysorg/sglang:v0.5.16-xeon",
        "image_id": "sha256:abc",
        "image_digest": "lmsysorg/sglang@sha256:def",
        "sglang_version": "0.5.16",
        "installed_source": "/opt/.venv/lib/python3.12/site-packages/sglang",
        "pyth_core_path": "/opt/.venv/lib/python3.12/site-packages/reactivity/hmr/core.py",
        "pyth_core_sha256": "0" * 64,
    }
    return argparse.Namespace(**(base | overrides))


def test_image_metadata_pins_the_image_the_distribution_and_the_hmr_core():
    metadata = image_metadata(metadata_args())
    assert metadata["official_image"] == "lmsysorg/sglang:v0.5.16-xeon"
    assert metadata["official_image_id"] == "sha256:abc"
    assert metadata["official_image_digest"].startswith("lmsysorg/sglang@sha256:")
    assert metadata["sglang_distribution_version"] == "0.5.16"
    assert "site-packages/sglang" in metadata["installed_distribution_source"]
    assert metadata["pyth_core_sha256"] == "0" * 64


def test_image_metadata_pins_the_pyth_on_line_commit_the_dockerfile_builds():
    """The receipt's HMR-core provenance is only meaningful if it matches what run.sh builds."""
    from smoke import PYTH_SHA

    metadata = image_metadata(metadata_args())
    assert metadata["pyth_on_line_sha"] == PYTH_SHA
    assert f'PYTH_ON_LINE_SHA="{PYTH_SHA}"' in (Path(__file__).parent.parent / "run.sh").read_text(encoding="utf-8")


def test_image_metadata_version_agrees_with_the_base_image_tag():
    metadata = image_metadata(metadata_args())
    assert metadata["sglang_distribution_version"] in metadata["official_image"]


# --- receipt durability ---


def test_write_receipt_round_trips_a_failed_receipt(tmp_path: Path):
    path = write_receipt(tmp_path / "r.json", {"status": "failed", "error": "AssertionError: x"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "failed", "error": "AssertionError: x"}


def test_smoke_writes_the_receipt_when_the_launcher_never_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """End-to-end through `run`: `Popen` fails, and the receipt must still land on disk."""
    import smoke

    source = tmp_path / "src"
    for relative in (TARGET, DEPENDENT_PATH):
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_text("x = 1\n", encoding="utf-8")
    results = tmp_path / "results"
    monkeypatch.setattr("shutil.which", lambda name: "/nonexistent/sglang-hmr" if name == "sglang-hmr" else None)
    monkeypatch.setattr(smoke.subprocess, "Popen", lambda *_, **__: (_ for _ in ()).throw(OSError("no such file")))
    args = metadata_args(source=str(source), results=str(results), model="m", port=31000, startup_timeout=1)
    with pytest.raises(OSError, match="no such file"):
        smoke.run(args)
    receipt = json.loads((results / "cpu-smoke-receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert "OSError" in receipt["error"]
    assert receipt["server_stopped"] is True
    assert receipt["source_restored"] is True  # nothing was mutated, so the target is untouched
    assert receipt["official_image"] == "lmsysorg/sglang:v0.5.16-xeon"


# --- teardown races ---


def test_teardown_tolerates_a_group_that_is_already_gone(monkeypatch: pytest.MonkeyPatch):
    """`poll()` said running, the group died, then we signalled: that must not mask the real error."""
    waits: list[float] = []
    monkeypatch.setattr(os, "killpg", lambda *_: (_ for _ in ()).throw(ProcessLookupError()), raising=False)
    process = FakeProcess(pid=4321, on_wait=lambda timeout: waits.append(timeout))
    teardown(process)
    assert waits == []  # nothing left to reap


@pytest.mark.skipif(not _SUPPORTS_SIGKILL, reason="SIGKILL not available on Windows")
def test_teardown_escalates_to_sigkill_when_sigterm_times_out(monkeypatch: pytest.MonkeyPatch):
    signals: list[int] = []
    monkeypatch.setattr(os, "killpg", lambda _pid, number: signals.append(number), raising=False)

    def on_wait(timeout: float) -> None:
        if len(signals) == 1:
            raise subprocess.TimeoutExpired(cmd="sglang", timeout=timeout)

    teardown(FakeProcess(pid=4321, on_wait=on_wait), term_timeout=1, kill_timeout=1)
    assert signals == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.skipif(not _SUPPORTS_SIGKILL, reason="SIGKILL not available on Windows")
def test_teardown_tolerates_the_group_dying_between_sigterm_timeout_and_sigkill(monkeypatch: pytest.MonkeyPatch):
    calls: list[int] = []

    def killpg(_pid: int, number: int) -> None:
        calls.append(number)
        if number == signal.SIGKILL:
            raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", killpg, raising=False)
    waits: list[float] = []

    def on_wait(timeout: float) -> None:
        waits.append(timeout)
        if len(waits) == 1:
            raise subprocess.TimeoutExpired(cmd="sglang", timeout=timeout)

    teardown(FakeProcess(pid=4321, on_wait=on_wait), term_timeout=1, kill_timeout=2)
    assert calls == [signal.SIGTERM, signal.SIGKILL]
    assert waits == [1, 2]  # the second wait still reaps the child


def test_teardown_signals_the_group_not_the_single_pid(monkeypatch: pytest.MonkeyPatch):
    """SGLang spawns scheduler children; killing only the launcher leaks them past the container."""
    targets: list[int] = []
    monkeypatch.setattr(os, "killpg", lambda pid, _number: targets.append(pid), raising=False)
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("teardown must not signal a single pid"))
    teardown(FakeProcess(pid=4321, on_wait=lambda _: None))
    assert targets == [4321]


def test_teardown_returns_none_on_clean_exit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(os, "killpg", lambda *_: None, raising=False)
    result = teardown(FakeProcess(pid=4321, on_wait=lambda _: None))
    assert result is None


@pytest.mark.skipif(not _SUPPORTS_SIGKILL, reason="SIGKILL not available on Windows")
def test_teardown_returns_an_error_when_sigkill_times_out(monkeypatch: pytest.MonkeyPatch):
    signals: list[int] = []
    monkeypatch.setattr(os, "killpg", lambda _pid, number: signals.append(number), raising=False)

    def on_wait(timeout: float) -> None:
        raise subprocess.TimeoutExpired(cmd="sglang", timeout=timeout)

    result = teardown(FakeProcess(pid=4321, on_wait=on_wait), term_timeout=1, kill_timeout=1)
    assert result is not None
    assert "survived SIGKILL" in result
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_teardown_returns_an_error_on_unexpected_exception(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(os, "killpg", lambda *_: (_ for _ in ()).throw(PermissionError("denied")), raising=False)
    result = teardown(FakeProcess(pid=4321, on_wait=lambda _: None))
    assert result is not None
    assert "PermissionError" in result


@pytest.mark.skipif(not _SUPPORTS_SIGKILL, reason="SIGKILL not available on Windows")
def test_smoke_writes_teardown_error_to_receipt_on_sigkill_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """End-to-end: a process that survives SIGKILL must mark the receipt as failed and record the error."""
    import smoke

    source = tmp_path / "src"
    for relative in (TARGET, DEPENDENT_PATH):
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_text("x = 1\n", encoding="utf-8")
    results = tmp_path / "results"
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sglang-hmr" if name == "sglang-hmr" else None)

    class UnkillableProcess:
        def __init__(self):
            self.pid = 9999
            self.returncode = None

        def poll(self) -> int | None:
            return None  # always reports running

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired(cmd="sglang", timeout=timeout or 0)

    def fake_popen(*_, **__):
        return UnkillableProcess()

    monkeypatch.setattr(smoke.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(smoke, "wait_ready", lambda *_: None)
    monkeypatch.setattr(smoke, "generate", lambda _: (503, {}, {"error": "startup incomplete"}))
    monkeypatch.setattr(os, "killpg", lambda *_: None, raising=False)
    args = metadata_args(source=str(source), results=str(results), model="m", port=31000, startup_timeout=1)
    with pytest.raises(AssertionError):  # the smoke will fail on the 503
        smoke.run(args)
    receipt = json.loads((results / "cpu-smoke-receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert "teardown_error" in receipt
    assert "survived SIGKILL" in receipt["teardown_error"]


def test_smoke_preserves_inherited_sglang_plugins_and_appends_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The example's probe must not clobber plugins the user set in the environment."""
    import smoke

    source = tmp_path / "src"
    for relative in (TARGET, DEPENDENT_PATH):
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_text("x = 1\n", encoding="utf-8")
    results = tmp_path / "results"
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sglang-hmr" if name == "sglang-hmr" else None)
    monkeypatch.setenv("SGLANG_PLUGINS", "user_plugin_a,user_plugin_b")
    captured_env: dict[str, str] = {}

    def fake_popen(*_, env, **__):
        captured_env.update(env)
        raise OSError("stop here")

    monkeypatch.setattr(smoke.subprocess, "Popen", fake_popen)
    args = metadata_args(source=str(source), results=str(results), model="m", port=31000, startup_timeout=1)
    with pytest.raises(OSError, match="stop here"):
        smoke.run(args)
    assert "SGLANG_PLUGINS" in captured_env
    plugins = captured_env["SGLANG_PLUGINS"].split(",")
    assert "user_plugin_a" in plugins
    assert "user_plugin_b" in plugins
    assert "hmr_sglang_probe" in plugins


def test_smoke_dedupes_probe_when_already_in_inherited_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import smoke

    source = tmp_path / "src"
    for relative in (TARGET, DEPENDENT_PATH):
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_text("x = 1\n", encoding="utf-8")
    results = tmp_path / "results"
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sglang-hmr" if name == "sglang-hmr" else None)
    monkeypatch.setenv("SGLANG_PLUGINS", "user_plugin,hmr_sglang_probe,other")
    captured_env: dict[str, str] = {}

    def fake_popen(*_, env, **__):
        captured_env.update(env)
        raise OSError("stop here")

    monkeypatch.setattr(smoke.subprocess, "Popen", fake_popen)
    args = metadata_args(source=str(source), results=str(results), model="m", port=31000, startup_timeout=1)
    with pytest.raises(OSError, match="stop here"):
        smoke.run(args)
    plugins = captured_env["SGLANG_PLUGINS"].split(",")
    assert plugins.count("hmr_sglang_probe") == 1


def test_smoke_handles_empty_inherited_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import smoke

    source = tmp_path / "src"
    for relative in (TARGET, DEPENDENT_PATH):
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_text("x = 1\n", encoding="utf-8")
    results = tmp_path / "results"
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sglang-hmr" if name == "sglang-hmr" else None)
    monkeypatch.setenv("SGLANG_PLUGINS", "")
    captured_env: dict[str, str] = {}

    def fake_popen(*_, env, **__):
        captured_env.update(env)
        raise OSError("stop here")

    monkeypatch.setattr(smoke.subprocess, "Popen", fake_popen)
    args = metadata_args(source=str(source), results=str(results), model="m", port=31000, startup_timeout=1)
    with pytest.raises(OSError, match="stop here"):
        smoke.run(args)
    assert captured_env["SGLANG_PLUGINS"] == "hmr_sglang_probe"


class FakeProcess:
    """Only what the launch/teardown helpers touch: a pid, `poll`, and a `wait` that can time out."""

    def __init__(self, pid: int, on_wait: Callable[[float], None]):
        self.pid = pid
        self.returncode: int | None = 0
        self._on_wait = on_wait

    def wait(self, timeout: float | None = None) -> int:
        self._on_wait(0.0 if timeout is None else timeout)
        return 0

    def poll(self) -> int | None:
        return None


# --- marker, mutation, and load-line helpers ---


def test_print_statement_is_one_line_and_carries_the_pid():
    from mutations import MARKER, print_statement

    statement = print_statement()
    assert "\n" not in statement
    assert MARKER in statement and "getpid()" in statement and "flush=True" in statement


@pytest.mark.parametrize("marker", ["bad/marker", "HMR_PROBE_OTHER", "HMR_PROBE_SGLANG_with space", ""])
def test_print_statement_rejects_a_marker_that_could_collide_or_break_the_line(marker: str):
    from mutations import print_statement

    with pytest.raises(ValueError, match="alphanumeric HMR_PROBE_SGLANG_ token"):
        print_statement(marker)


def test_marker_lines_returns_only_matching_lines():
    from mutations import marker_lines

    log = "before\nHMR_PROBE_SGLANG_X pid=7\nnoise\nHMR_PROBE_SGLANG_X pid=9\n"
    assert marker_lines(log, "HMR_PROBE_SGLANG_X") == ["HMR_PROBE_SGLANG_X pid=7", "HMR_PROBE_SGLANG_X pid=9"]


def test_marker_lines_is_empty_before_the_mutation_runs():
    from mutations import marker_lines

    assert marker_lines("startup log with no marker\n", "HMR_PROBE_SGLANG_X") == []


def test_the_inserted_marker_is_unique_in_the_mutated_file(tmp_path: Path):
    """The smoke asserts exactly one occurrence, which is what makes the log line traceable."""
    from mutations import MARKER, MutationSet, print_statement

    target = tmp_path / "m.py"
    target.write_text("def has_forward_context():\n    return True\n", encoding="utf-8")
    statement = print_statement(MARKER)
    with MutationSet(tmp_path) as edits:
        edits.insert_statement("m.py", "has_forward_context", statement)
        text = target.read_text(encoding="utf-8")
        assert text.count(MARKER) == 1
        assert text.count(statement) == 1


@pytest.mark.parametrize("needle", ["load weight begin", "load weight end", "loading model weights", "weight loading"])
def test_load_lines_detects_every_reload_phrase_the_smoke_forbids(needle: str):
    assert load_lines(f"prefix\nINFO {needle} suffix\n") == [f"INFO {needle} suffix"]


def test_load_lines_is_case_insensitive():
    """SGLang's own logs are not consistently cased, so a case-sensitive check would pass vacuously."""
    assert load_lines("Load Weight Begin\n") == ["Load Weight Begin"]


def test_load_lines_ignores_unrelated_lines():
    assert load_lines("Capacity of new memory pool\nDecode batch\n") == []


def test_load_lines_is_empty_for_an_empty_log():
    assert load_lines("") == []


# --- MutationSet exact-byte restore ---


def test_mutation_set_restores_bytes_exactly_including_line_endings(tmp_path: Path):
    """A text-mode round trip would normalize CRLF, so "restored" would be a weaker claim."""
    from mutations import MutationSet

    target = tmp_path / "f.py"
    original = b"def f():\r\n    return 1\r\n\r\n# trailing\r\n"
    target.write_bytes(original)
    with MutationSet(tmp_path) as edits:
        edits._save("f.py")  # noqa: SLF001 - the save/restore pair is the unit under test
        target.write_bytes(b"clobbered")
    assert target.read_bytes() == original


def test_mutation_set_restores_after_a_real_insertion(tmp_path: Path):
    from mutations import MutationSet

    target = tmp_path / "f.py"
    original = b'def has_forward_context():\n    """doc"""\n    return True\n'
    target.write_bytes(original)
    with MutationSet(tmp_path) as edits:
        edits.insert_statement("f.py", "has_forward_context", "print(1)")
        assert target.read_bytes() != original
    assert target.read_bytes() == original


def test_mutation_set_restores_when_the_body_raises(tmp_path: Path):
    """Every smoke assertion runs inside the `with`, so restore must survive an exception."""
    from mutations import MutationSet

    target = tmp_path / "f.py"
    target.write_bytes(b"def f():\n    return 1\n")
    with pytest.raises(AssertionError, match="assertion inside the mutation"), MutationSet(tmp_path) as edits:
        edits.insert_statement("f.py", "f", "print(1)")
        raise AssertionError("assertion inside the mutation")
    assert target.read_bytes() == b"def f():\n    return 1\n"


def test_mutation_set_deletes_a_file_it_created(tmp_path: Path):
    from mutations import MutationSet

    created = tmp_path / "new.py"
    with MutationSet(tmp_path) as edits:
        edits._save("new.py")  # noqa: SLF001 - saving a not-yet-existing path is the case under test
        created.write_text("x = 1\n", encoding="utf-8")
    assert not created.exists()


def test_mutation_set_refuses_a_path_outside_the_source_root(tmp_path: Path):
    from mutations import MutationSet

    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside.py").write_text("secret\n", encoding="utf-8")
    with MutationSet(root) as edits, pytest.raises(ValueError, match="escapes source root"):
        edits.insert_statement("../outside.py", "f", "print(1)")
    assert (tmp_path / "outside.py").read_text(encoding="utf-8") == "secret\n"


def test_mutation_set_insert_targets_the_named_function_only(tmp_path: Path):
    from mutations import MutationSet

    target = tmp_path / "f.py"
    target.write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n", encoding="utf-8")
    with MutationSet(tmp_path) as edits:
        edits.insert_statement("f.py", "b", "print('b')")
        lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "    return 1"  # `a` is untouched
    assert "print('b')" in lines[5]


def test_mutation_set_insert_goes_after_a_docstring(tmp_path: Path):
    """Inserting before a docstring would move it out of position and change the module's semantics."""
    from mutations import MutationSet

    target = tmp_path / "f.py"
    target.write_text('def f():\n    """doc"""\n    return 1\n', encoding="utf-8")
    with MutationSet(tmp_path) as edits:
        line = edits.insert_statement("f.py", "f", "print(1)")
        lines = target.read_text(encoding="utf-8").splitlines()
    assert line == 3
    assert lines[1].strip() == '"""doc"""'
    assert lines[2].strip() == "print(1)"


def test_mutation_set_insert_rejects_an_ambiguous_function_name(tmp_path: Path):
    from mutations import MutationSet

    target = tmp_path / "f.py"
    target.write_text("def f():\n    return 1\n\n\ndef f():\n    return 2\n", encoding="utf-8")
    with MutationSet(tmp_path) as edits, pytest.raises(AssertionError, match="expected one function"):
        edits.insert_statement("f.py", "f", "print(1)")


def test_mutation_set_insert_rejects_an_absent_function(tmp_path: Path):
    from mutations import MutationSet

    target = tmp_path / "f.py"
    target.write_text("x = 1\n", encoding="utf-8")
    with MutationSet(tmp_path) as edits, pytest.raises(AssertionError, match="expected one function"):
        edits.insert_statement("f.py", "absent", "print(1)")


def test_mutation_set_insert_preserves_tab_indentation(tmp_path: Path):
    """A space-indented insertion into a tab-indented body is a TabError at import time."""
    import ast

    from mutations import MutationSet

    target = tmp_path / "f.py"
    target.write_text("def f():\n\treturn 1\n", encoding="utf-8")
    with MutationSet(tmp_path) as edits:
        edits.insert_statement("f.py", "f", "print(1)")
        text = target.read_text(encoding="utf-8")
        inserted = text.splitlines()[1]
        assert inserted == "\tprint(1)"
        assert " " not in inserted[: len(inserted) - len(inserted.lstrip())]
        ast.parse(text)  # a mixed-indentation insert raises TabError here


def test_mutation_set_insert_preserves_nested_tab_indentation(tmp_path: Path):
    from mutations import MutationSet

    target = tmp_path / "f.py"
    target.write_text("class C:\n\tdef f(self):\n\t\treturn 1\n", encoding="utf-8")
    with MutationSet(tmp_path) as edits:
        edits.insert_statement("f.py", "f", "print(1)", class_name="C")
        assert target.read_text(encoding="utf-8").splitlines()[2] == "\t\tprint(1)"


def test_mutation_set_insert_preserves_tab_indentation_after_a_docstring(tmp_path: Path):
    from mutations import MutationSet

    target = tmp_path / "f.py"
    target.write_text('def f():\n\t"""doc"""\n\treturn 1\n', encoding="utf-8")
    with MutationSet(tmp_path) as edits:
        edits.insert_statement("f.py", "f", "print(1)")
        lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[1] == '\t"""doc"""'
    assert lines[2] == "\tprint(1)"


def test_mutation_set_insert_refuses_a_body_mixing_tabs_and_spaces(tmp_path: Path):
    """Neither choice would be right, so guessing is worse than refusing."""
    from mutations import MutationSet

    target = tmp_path / "f.py"
    # The body's own leading whitespace is a tab followed by a space, so neither indent character
    # is the file's convention. Python accepts this, which is exactly why it has to be caught here.
    target.write_text("def f():\n\t return 1\n", encoding="utf-8")
    with MutationSet(tmp_path) as edits, pytest.raises(ValueError, match="mixes tabs and spaces"):
        edits.insert_statement("f.py", "f", "print(1)")


def test_mutation_set_insert_refuses_a_single_line_function_body(tmp_path: Path):
    """`def f(): return 1` has no body indentation to copy."""
    from mutations import MutationSet

    target = tmp_path / "f.py"
    target.write_text("def f(): return 1\n", encoding="utf-8")
    with MutationSet(tmp_path) as edits, pytest.raises(ValueError, match="body on the `def` line"):
        edits.insert_statement("f.py", "f", "print(1)")


def test_mutation_set_insert_restores_bytes_after_a_refusal(tmp_path: Path):
    """A refusal happens after `_save`, so the file must still be restored untouched."""
    from mutations import MutationSet

    target = tmp_path / "f.py"
    original = b"def f(): return 1\n"
    target.write_bytes(original)
    with MutationSet(tmp_path) as edits, pytest.raises(ValueError):
        edits.insert_statement("f.py", "f", "print(1)")
    assert target.read_bytes() == original


def test_mutation_set_insert_keeps_the_file_parseable(tmp_path: Path):
    import ast

    from mutations import MARKER, MutationSet, print_statement

    target = tmp_path / "f.py"
    target.write_text("def has_forward_context():\n    return True\n", encoding="utf-8")
    with MutationSet(tmp_path) as edits:
        edits.insert_statement("f.py", "has_forward_context", print_statement(MARKER))
        ast.parse(target.read_text(encoding="utf-8"))  # a syntax error here would be rejected by preflight


def test_mutation_set_changes_exactly_one_file(source_root: Path):
    """The smoke's "exactly one changed Python source" claim rests on this."""
    from mutations import MutationSet
    from smoke import python_source_hashes

    before = python_source_hashes(source_root)
    (source_root / TARGET).write_text("def has_forward_context():\n    return True\n", encoding="utf-8")
    baseline = python_source_hashes(source_root)
    with MutationSet(source_root) as edits:
        edits.insert_statement(TARGET, "has_forward_context", "print(1)")
        changed = sorted(key for key, digest in python_source_hashes(source_root).items() if baseline.get(key) != digest)
    assert changed == [TARGET]
    assert set(before) == set(baseline)


# --- process cmdline reading ---


def test_read_process_cmdline_splits_on_nul(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The kernel writes argv as NUL-separated tokens, not space-separated."""
    from smoke import read_process_cmdline

    fake_proc = tmp_path / "proc" / "1234" / "cmdline"
    fake_proc.parent.mkdir(parents=True)
    fake_proc.write_bytes(b"sglang\x00serve\x00facebook/opt-125m\x00--port\x0031000\x00")
    monkeypatch.setattr("smoke.Path", lambda p: fake_proc if str(p) == "/proc/1234/cmdline" else Path(p))
    assert read_process_cmdline(1234) == ["sglang", "serve", "facebook/opt-125m", "--port", "31000"]


def test_read_process_cmdline_raises_when_proc_is_missing(monkeypatch: pytest.MonkeyPatch):
    """Without /proc, the cmdline verification cannot run and must fail loudly."""
    from smoke import read_process_cmdline

    monkeypatch.setattr("smoke.Path", lambda p: Path("/nonexistent") if "/proc/" in str(p) else Path(p))
    with pytest.raises(RuntimeError, match="/proc not available"):
        read_process_cmdline(9999)


def test_assert_official_argv_accepts_valid_exec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from smoke import assert_official_argv

    fake_proc = tmp_path / "proc" / "1234" / "cmdline"
    fake_proc.parent.mkdir(parents=True)
    fake_proc.write_bytes(b"sglang\x00serve\x00facebook/opt-125m\x00--port\x0031000\x00")
    monkeypatch.setattr("smoke.Path", lambda p: fake_proc if str(p) == "/proc/1234/cmdline" else Path(p))
    cmdline = assert_official_argv(1234, ["serve", "facebook/opt-125m", "--port", "31000"])
    assert cmdline == ["sglang", "serve", "facebook/opt-125m", "--port", "31000"]


def test_assert_official_argv_accepts_python_console_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from smoke import assert_official_argv

    fake_proc = tmp_path / "proc" / "1234" / "cmdline"
    fake_proc.parent.mkdir(parents=True)
    fake_proc.write_bytes(b"/opt/.venv/bin/python3\x00/opt/.venv/bin/sglang\x00serve\x00model\x00")
    monkeypatch.setattr("smoke.Path", lambda p: fake_proc if str(p) == "/proc/1234/cmdline" else Path(p))
    assert assert_official_argv(1234, ["serve", "model"]) == ["/opt/.venv/bin/python3", "/opt/.venv/bin/sglang", "serve", "model"]


def test_assert_official_argv_rejects_wrong_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from smoke import assert_official_argv

    fake_proc = tmp_path / "proc" / "1234" / "cmdline"
    fake_proc.parent.mkdir(parents=True)
    fake_proc.write_bytes(b"python\x00-m\x00sglang.cli\x00serve\x00model\x00")
    monkeypatch.setattr("smoke.Path", lambda p: fake_proc if str(p) == "/proc/1234/cmdline" else Path(p))
    with pytest.raises(AssertionError, match="not exec'd into the official sglang console script"):
        assert_official_argv(1234, ["serve", "model"])


def test_assert_official_argv_rejects_hmr_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from smoke import assert_official_argv

    fake_proc = tmp_path / "proc" / "1234" / "cmdline"
    fake_proc.parent.mkdir(parents=True)
    fake_proc.write_bytes(b"sglang\x00serve\x00model\x00--hmr-source-root\x00/src\x00")
    monkeypatch.setattr("smoke.Path", lambda p: fake_proc if str(p) == "/proc/1234/cmdline" else Path(p))
    with pytest.raises(AssertionError, match="HMR-only options leaked"):
        assert_official_argv(1234, ["serve", "model"])


# --- telemetry readers ---


def test_published_record_returns_the_latest_publication_for_the_target():
    events = [{"kind": "published", "path": TARGET, "t": 1}, {"kind": "published", "path": TARGET, "t": 5}]
    record = published_record(snapshot(*events))
    assert record is not None and record["t"] == 5


def test_published_record_ignores_another_path():
    """Only the mutated target counts: a publication of the dependent is not the signal waited on."""
    assert published_record(snapshot({"kind": "published", "path": DEPENDENT_PATH, "t": 1})) is None


def test_published_record_is_none_without_a_publication():
    assert published_record(snapshot({"kind": "source_change", "path": TARGET, "t": 1})) is None


def test_queued_before_requires_the_watcher_event_to_precede_the_publication():
    events = [{"kind": "source_change", "path": TARGET, "t": 2}]
    assert queued_before(snapshot(*events), {"t": 3}) is not None
    assert queued_before(snapshot(*events), {"t": 1}) is None


def test_queued_before_accepts_an_equal_timestamp():
    """Same-tick ordering is not a violation: the watcher queued it, then published in that pass."""
    assert queued_before(snapshot({"kind": "source_change", "path": TARGET, "t": 2}), {"t": 2}) is not None


def test_rejected_records_collects_both_rejections_and_watcher_failures():
    events = [{"kind": "rejected", "path": TARGET}, {"kind": "published", "path": TARGET}, {"kind": "watcher_failed", "error": "x"}]
    assert [event["kind"] for event in rejected_records(snapshot(*events))] == ["rejected", "watcher_failed"]


def test_rejected_records_is_empty_on_a_healthy_run():
    assert rejected_records(snapshot({"kind": "published", "path": TARGET})) == []


def test_identity_covers_what_a_reload_or_restart_would_change():
    fields = {"pid": 1, "ppid": 0, "model_runner_id": 2, "model_id": 3, "model_class_id": 4, "model_class": "M", "first_parameter_data_ptr": 5, "weight_load_time": 6.0}
    assert identity(fields | {"modules": {}, "hmr": {}}) == fields


def test_identity_raises_when_a_field_the_assertion_needs_is_absent():
    """A silently missing key would make the before/after comparison pass vacuously."""
    with pytest.raises(KeyError):
        identity({"pid": 1})


# --- polling loops, driven through their injected dependencies ---


def test_wait_ready_returns_once_health_generate_answers_200(monkeypatch: pytest.MonkeyPatch):
    attempts = []
    monkeypatch.setattr("smoke.request_json", lambda *_, **__: (attempts.append(1), (200 if len(attempts) > 2 else 503, {}, None))[1])
    monkeypatch.setattr(time, "sleep", lambda _: None)
    wait_ready("http://127.0.0.1:31000", FakeProcess(pid=1, on_wait=lambda _: None), timeout=10)
    assert len(attempts) == 3


def test_wait_ready_fails_fast_when_the_server_exits_during_startup(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("smoke.request_json", lambda *_, **__: pytest.fail("must not poll a dead process"))

    class Exited(FakeProcess):
        """A real `Popen` sets `returncode` when `poll` observes the exit; so must the double."""

        def __init__(self, pid: int, on_wait: Callable[[float], None]):
            super().__init__(pid, on_wait)
            self.returncode: int | None = 3

        def poll(self) -> int | None:
            return 3

    with pytest.raises(RuntimeError, match="exited during startup with 3"):
        wait_ready("http://127.0.0.1:31000", Exited(pid=1, on_wait=lambda _: None), timeout=10)


def test_wait_ready_times_out_with_the_last_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("smoke.request_json", lambda *_, **__: (_ for _ in ()).throw(OSError("connection refused")))
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(TimeoutError, match="connection refused"):
        wait_ready("http://127.0.0.1:31000", FakeProcess(pid=1, on_wait=lambda _: None), timeout=0.01)


def test_wait_published_returns_the_publication_and_its_queue_event(monkeypatch: pytest.MonkeyPatch):
    queued = {"kind": "source_change", "path": TARGET, "t": 1}
    publication = {"kind": "published", "path": TARGET, "t": 2, "forced_dependents_reexecuted": [DEPENDENT]}
    polls: list[int] = []

    def fake_probe(_base: str) -> dict[str, Any]:
        polls.append(1)
        events = [queued] if len(polls) < 3 else [queued, publication]
        return {"hmr": snapshot(*events)}

    monkeypatch.setattr("smoke.probe", fake_probe)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    state, found_publication, found_queued = wait_published("http://127.0.0.1:31000", timeout=10)
    assert found_publication == publication
    assert found_queued == queued
    assert state["installed"]


def test_wait_published_rejects_a_publication_with_no_preceding_watcher_event(monkeypatch: pytest.MonkeyPatch):
    """Without this, a publication from any other cause would count as watcher-driven."""
    monkeypatch.setattr("smoke.probe", lambda _: {"hmr": snapshot({"kind": "published", "path": TARGET, "t": 2})})
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(AssertionError, match="published with no preceding watcher event"):
        wait_published("http://127.0.0.1:31000", timeout=10)


def test_wait_published_fails_on_a_rejection_instead_of_waiting_out_the_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("smoke.probe", lambda _: {"hmr": snapshot({"kind": "rejected", "path": TARGET, "error": "SyntaxError"})})
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(AssertionError, match="refused to publish"):
        wait_published("http://127.0.0.1:31000", timeout=10)


def test_wait_published_fails_on_a_watcher_failure(monkeypatch: pytest.MonkeyPatch):
    """A dead watcher would otherwise look identical to "the edit has not landed yet"."""
    monkeypatch.setattr("smoke.probe", lambda _: {"hmr": snapshot({"kind": "watcher_failed", "error": "OSError"})})
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(AssertionError, match="refused to publish"):
        wait_published("http://127.0.0.1:31000", timeout=10)


def test_wait_published_times_out_reporting_pending_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("smoke.probe", lambda _: {"hmr": {"pending": [{"path": TARGET}], "installed": True, "telemetry": {"events": []}}})
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(TimeoutError, match="did not publish"):
        wait_published("http://127.0.0.1:31000", timeout=0.01)
