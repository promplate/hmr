"""Runtime contract: watcher health, pending retention, hook semantics, module origin.

`sync_pending` is driven with a fake `reactivity.hmr` surface rather than a live HMR install:
the decisions under test are this module's own (what it refuses, what it keeps pending, what
it reports), and a real loader would make the failure modes unreachable.
"""

from __future__ import annotations

import sys
import threading
from collections import deque
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from sglang_hmr.runtime import bootstrap, telemetry
from sglang_hmr.runtime.scope import DEPENDENT, TARGET, Manifest

if TYPE_CHECKING:
    from collections.abc import Iterator


class FakeLoad:
    """Stands in for the private loader handle `_load_handle` returns."""

    def __init__(self, on_call=None):
        self.dirty = False
        self.calls = 0
        self.invalidations = 0
        self._on_call = on_call

    def invalidate(self) -> None:
        self.invalidations += 1

    def __call__(self) -> None:
        self.calls += 1
        if self._on_call is not None:
            self._on_call()


class FakeModule:
    def __init__(self, name: str, file: str | None, load: FakeLoad):
        self.__name__ = name
        self.__file__ = file
        self.load = load


class FakeBatch:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    (root / TARGET).parent.mkdir(parents=True, exist_ok=True)
    (root / TARGET).write_text("x = 1\n", encoding="utf-8")
    return root


@pytest.fixture
def manifest(source_root: Path) -> Manifest:
    return Manifest(source_root, ({"path": TARGET, "sha256": "0" * 64},), (TARGET,), (TARGET,), {TARGET: (DEPENDENT,)})


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch, manifest: Manifest) -> Iterator[SimpleNamespace]:
    """An installed-looking runtime whose HMR surface is fake, and whose globals are restored."""
    hooks = SimpleNamespace(pre_calls=0, post_calls=0, pre_error=None, post_error=None)
    modules: dict[Path, Any] = {}

    def call_pre_reload_hooks() -> None:
        hooks.pre_calls += 1
        if hooks.pre_error is not None:
            raise hooks.pre_error

    def call_post_reload_hooks() -> None:
        hooks.post_calls += 1
        if hooks.post_error is not None:
            raise hooks.post_error

    core = ModuleType("reactivity.hmr.core")
    core.HMR_CONTEXT = SimpleNamespace(batch=FakeBatch)  # pyright: ignore[reportAttributeAccessIssue]
    core.get_path_module_map = lambda: modules  # pyright: ignore[reportAttributeAccessIssue]
    core.patch_meta_path = lambda **_: None  # pyright: ignore[reportAttributeAccessIssue]
    hooks_module = ModuleType("reactivity.hmr.hooks")
    hooks_module.call_pre_reload_hooks = call_pre_reload_hooks  # pyright: ignore[reportAttributeAccessIssue]
    hooks_module.call_post_reload_hooks = call_post_reload_hooks  # pyright: ignore[reportAttributeAccessIssue]
    parent = ModuleType("reactivity.hmr")
    parent.core = core  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setitem(sys.modules, "reactivity", ModuleType("reactivity"))
    monkeypatch.setitem(sys.modules, "reactivity.hmr", parent)
    monkeypatch.setitem(sys.modules, "reactivity.hmr.core", core)
    monkeypatch.setitem(sys.modules, "reactivity.hmr.hooks", hooks_module)
    monkeypatch.setattr(bootstrap, "_load_handle", lambda module: module.load)
    monkeypatch.setattr(bootstrap, "_INSTALLED", True)
    monkeypatch.setattr(bootstrap, "_MANIFEST", manifest)
    monkeypatch.setattr(bootstrap, "_PENDING", {})
    monkeypatch.setattr(bootstrap, "_WATCHER_ERROR", None)
    monkeypatch.setattr(bootstrap, "_WATCH_THREAD", None)
    # The event deque is module-global, so without this a later test would assert against
    # events an earlier one emitted.
    monkeypatch.setattr(telemetry, "_EVENTS", deque(maxlen=200))
    yield SimpleNamespace(hooks=hooks, modules=modules, manifest=manifest, source_root=manifest.source_root)


def queue(path: Path, relative: str = TARGET, seen_at: float = 1.0) -> None:
    bootstrap._PENDING[path.resolve()] = {"path": relative, "seen_at": seen_at}  # noqa: SLF001 - the pending map is the unit under test


DEFAULT_FILE = object()  # distinguishes "use the path" from an explicit `__file__` of None


def register(runtime: SimpleNamespace, path: Path, name: str, load: FakeLoad, file: str | None | object = DEFAULT_FILE) -> FakeModule:
    module = FakeModule(name, str(path) if file is DEFAULT_FILE else file, load)  # pyright: ignore[reportArgumentType]
    runtime.modules[path.resolve()] = module
    return module


# --- watcher health must reach `state()` ---


@pytest.mark.usefixtures("runtime")
def test_state_is_unhealthy_after_the_watcher_thread_raises(monkeypatch: pytest.MonkeyPatch):
    """A dead watcher can no longer see any edit, so `installed: True` alone must not read as healthy."""

    def exploding_watch(*_args: object, **_kwargs: object):
        raise OSError("inotify watch limit reached")

    monkeypatch.setattr(bootstrap, "watch", exploding_watch)
    thread = threading.Thread(target=bootstrap._watch)  # noqa: SLF001 - running the watcher body is the point
    thread.start()
    thread.join(timeout=5)
    state = bootstrap.state()
    assert state["installed"] is True
    assert state["watching"] is False
    assert state["watcher_error"] is not None and "inotify watch limit reached" in state["watcher_error"]
    assert state["healthy"] is False


@pytest.mark.usefixtures("runtime")
def test_state_records_the_watcher_failure_as_an_event(monkeypatch: pytest.MonkeyPatch):
    def dying_watch(*_args: object, **_kwargs: object):
        raise RuntimeError("watch died")

    monkeypatch.setattr(bootstrap, "watch", dying_watch)
    thread = threading.Thread(target=bootstrap._watch)  # noqa: SLF001
    thread.start()
    thread.join(timeout=5)
    kinds = [event["kind"] for event in bootstrap.state()["telemetry"]["events"]]
    assert "watcher_failed" in kinds


@pytest.mark.usefixtures("runtime")
def test_state_is_unhealthy_when_the_watcher_thread_is_absent():
    """No thread at all is the same operational fact as a crashed one."""
    state = bootstrap.state()
    assert state["watching"] is False
    assert state["healthy"] is False


@pytest.mark.usefixtures("runtime")
def test_state_is_healthy_with_a_live_watcher_thread_and_no_error(monkeypatch: pytest.MonkeyPatch):
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, daemon=True)
    thread.start()
    monkeypatch.setattr(bootstrap, "_WATCH_THREAD", thread)
    try:
        state = bootstrap.state()
        assert state["watching"] is True and state["watcher_error"] is None and state["healthy"] is True
    finally:
        stop.set()
        thread.join(timeout=5)


@pytest.mark.usefixtures("runtime")
def test_a_fork_clears_the_parents_watcher_error(monkeypatch: pytest.MonkeyPatch):
    """The child starts a fresh watcher, so inheriting the parent's error would report a lie."""
    monkeypatch.setattr(bootstrap, "_WATCHER_ERROR", "OSError: parent failure")
    monkeypatch.setattr(bootstrap, "_start_watcher", lambda: None)
    bootstrap._after_fork()  # noqa: SLF001 - the fork handler is the unit under test
    assert bootstrap._WATCHER_ERROR is None  # noqa: SLF001


# --- pre-hook failure must not lose the pending change ---


def test_a_failing_pre_hook_publishes_nothing(runtime: SimpleNamespace):
    runtime.hooks.pre_error = RuntimeError("user pre-hook blew up")
    target = runtime.source_root / TARGET
    load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    queue(target)
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert load.calls == 0  # nothing was re-executed


def test_a_failing_pre_hook_keeps_the_change_pending_for_retry(runtime: SimpleNamespace):
    """Draining then dropping would leave the process serving old code with an empty queue."""
    runtime.hooks.pre_error = RuntimeError("user pre-hook blew up")
    target = runtime.source_root / TARGET
    register(runtime, target, "sglang.srt.model_executor.forward_context", FakeLoad())
    queue(target)
    bootstrap.sync_pending()
    assert [record["path"] for record in bootstrap.state()["pending"]] == [TARGET]


def test_a_failing_pre_hook_is_reported_not_swallowed(runtime: SimpleNamespace):
    runtime.hooks.pre_error = RuntimeError("user pre-hook blew up")
    target = runtime.source_root / TARGET
    register(runtime, target, "sglang.srt.model_executor.forward_context", FakeLoad())
    queue(target)
    result = bootstrap.sync_pending()
    assert len(result["rejected"]) == 1
    assert "pre-reload hook failed" in result["rejected"][0]["error"]
    assert any(event["kind"] == "rejected" for event in bootstrap.state()["telemetry"]["events"])


def test_a_retried_change_publishes_once_the_pre_hook_recovers(runtime: SimpleNamespace):
    runtime.hooks.pre_error = RuntimeError("transient")
    target = runtime.source_root / TARGET
    load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, FakeLoad())
    queue(target)
    assert bootstrap.sync_pending()["published"] == []
    runtime.hooks.pre_error = None
    result = bootstrap.sync_pending()
    assert [item["path"] for item in result["published"]] == [TARGET]
    assert bootstrap.state()["pending"] == []


# --- post-hook semantics ---


def test_the_post_hook_runs_even_when_a_reload_is_rejected(runtime: SimpleNamespace):
    """User code that took a lock in the pre-hook must get its release, rejection or not."""
    target = runtime.source_root / TARGET
    queue(target)  # no module registered, so this is refused
    bootstrap.sync_pending()
    assert runtime.hooks.pre_calls == 1
    assert runtime.hooks.post_calls == 1


def test_a_failing_post_hook_does_not_unpublish_a_successful_reload(runtime: SimpleNamespace):
    """The new code is already live by then; reclassifying it as rejected would be false."""
    runtime.hooks.post_error = RuntimeError("user post-hook blew up")
    target = runtime.source_root / TARGET
    load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, FakeLoad())
    queue(target)
    result = bootstrap.sync_pending()
    assert [item["path"] for item in result["published"]] == [TARGET]
    assert result["rejected"] == []
    assert load.calls == 1


def test_a_failing_post_hook_is_reported_as_its_own_event(runtime: SimpleNamespace):
    runtime.hooks.post_error = RuntimeError("user post-hook blew up")
    target = runtime.source_root / TARGET
    register(runtime, target, "sglang.srt.model_executor.forward_context", FakeLoad())
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, FakeLoad())
    queue(target)
    bootstrap.sync_pending()
    events = {event["kind"] for event in bootstrap.state()["telemetry"]["events"]}
    assert "post_reload_hook_failed" in events


def test_a_failing_post_hook_does_not_requeue_a_published_change(runtime: SimpleNamespace):
    runtime.hooks.post_error = RuntimeError("user post-hook blew up")
    target = runtime.source_root / TARGET
    register(runtime, target, "sglang.srt.model_executor.forward_context", FakeLoad())
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, FakeLoad())
    queue(target)
    bootstrap.sync_pending()
    assert bootstrap.state()["pending"] == []


# --- rejections must be fail-closed, and retryable ones must stay pending ---


def test_a_syntax_error_is_refused_and_kept_pending(runtime: SimpleNamespace):
    """A half-written file is retryable: the finished write must still publish."""
    target = runtime.source_root / TARGET
    target.write_text("def broken(:\n", encoding="utf-8")
    load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    queue(target)
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert load.calls == 0  # the loader never saw the broken file
    assert "SyntaxError" in result["rejected"][0]["error"]
    assert [record["path"] for record in bootstrap.state()["pending"]] == [TARGET]


def test_a_fixed_file_publishes_on_the_next_pass(runtime: SimpleNamespace):
    target = runtime.source_root / TARGET
    target.write_text("def broken(:\n", encoding="utf-8")
    load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, FakeLoad())
    queue(target)
    bootstrap.sync_pending()
    target.write_text("def fixed():\n    return 1\n", encoding="utf-8")
    result = bootstrap.sync_pending()
    assert [item["path"] for item in result["published"]] == [TARGET]
    assert load.calls == 1


def test_a_raising_module_is_refused_and_kept_pending(runtime: SimpleNamespace):
    """The loader is now holding a failed module: continuing silently would serve a broken import."""
    target = runtime.source_root / TARGET
    load = FakeLoad(on_call=lambda: (_ for _ in ()).throw(ValueError("module body raised")))
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, FakeLoad())
    queue(target)
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert "ValueError: module body raised" in result["rejected"][0]["error"]
    assert [record["path"] for record in bootstrap.state()["pending"]] == [TARGET]


def test_an_unloaded_forced_dependent_refuses_the_publication(runtime: SimpleNamespace):
    """Swapping the provider without re-executing its importer leaves the old function live."""
    target = runtime.source_root / TARGET
    load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    queue(target)
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert f"forced dependent {DEPENDENT!r} is not loaded" in result["rejected"][0]["error"]


def test_an_unloaded_forced_dependent_is_checked_before_the_provider_is_reloaded(runtime: SimpleNamespace):
    """The refusal is hard (no retry), so reloading the provider first would leave the new provider
    live with its importer still holding the old function, with nothing pending to repair it."""
    target = runtime.source_root / TARGET
    load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    queue(target)
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert load.calls == 0  # no partial publication
    assert load.invalidations == 0
    assert bootstrap.state()["pending"] == []  # hard refusal: not retried


def test_a_forced_dependent_with_no_file_refuses_before_the_provider_is_reloaded(runtime: SimpleNamespace):
    """A dependent with no `__file__` cannot be shown to come from the watched tree."""
    target = runtime.source_root / TARGET
    load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, FakeLoad(), file=None)
    queue(target)
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert load.calls == 0
    assert f"forced dependent {DEPENDENT!r} has no __file__" in result["rejected"][0]["error"]


def test_a_forced_dependent_outside_the_source_root_refuses_before_the_provider_is_reloaded(runtime: SimpleNamespace, tmp_path: Path):
    """A site-packages copy of the importer: re-executing it would publish into a module the
    request path never uses, so the provider must not be swapped either."""
    target = runtime.source_root / TARGET
    load = FakeLoad()
    dependent_load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, dependent_load, file=str(tmp_path / "site-packages" / "sglang" / "model_runner.py"))
    queue(target)
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert load.calls == 0 and dependent_load.calls == 0
    assert "is not under source root" in result["rejected"][0]["error"]
    assert bootstrap.state()["pending"] == []


def test_a_raising_forced_dependent_refuses_the_publication_and_retries(runtime: SimpleNamespace):
    target = runtime.source_root / TARGET
    register(runtime, target, "sglang.srt.model_executor.forward_context", FakeLoad())
    dependent_load = FakeLoad(on_call=lambda: (_ for _ in ()).throw(ImportError("dependent raised")))
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, dependent_load)
    queue(target)
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert "ImportError: dependent raised" in result["rejected"][0]["error"]
    assert [record["path"] for record in bootstrap.state()["pending"]] == [TARGET]


def test_a_path_outside_auto_paths_is_refused_without_retry(runtime: SimpleNamespace):
    """The manifest forbids this, not the file, so retrying would loop forever."""
    other = runtime.source_root / "python" / "sglang" / "other.py"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("x = 1\n", encoding="utf-8")
    queue(other, relative="python/sglang/other.py")
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert result["rejected"][0]["error"] == "not in manifest auto_paths"
    assert bootstrap.state()["pending"] == []


def test_a_module_absent_from_the_map_is_refused_without_retry(runtime: SimpleNamespace):
    """The process imported it from elsewhere; a later pass would reach the same conclusion."""
    target = runtime.source_root / TARGET
    queue(target)
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert "not loaded from this source root" in result["rejected"][0]["error"]
    assert bootstrap.state()["pending"] == []


def test_a_rejection_never_reports_a_publication(runtime: SimpleNamespace):
    """Fail-closed: `published` is the only signal the example trusts, so it must stay empty."""
    target = runtime.source_root / TARGET
    load = FakeLoad(on_call=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    queue(target)
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert not any(event["kind"] == "published" for event in bootstrap.state()["telemetry"]["events"])


def test_requeue_does_not_clobber_a_newer_edit(runtime: SimpleNamespace):
    """The watcher may see a fresh write while `sync_pending` is failing on the old one."""
    target = runtime.source_root / TARGET
    load = FakeLoad(on_call=lambda: bootstrap._PENDING.setdefault(target.resolve(), {"path": TARGET, "seen_at": 99.0}) and None)  # noqa: SLF001
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, FakeLoad())
    queue(target, seen_at=1.0)
    bootstrap.sync_pending()
    pending = bootstrap.state()["pending"]
    assert len(pending) == 1
    assert pending[0]["seen_at"] == 99.0  # the newer sighting wins


# --- the live module's origin must match the watched path ---


def test_a_module_whose_file_points_elsewhere_is_refused(runtime: SimpleNamespace, tmp_path: Path):
    """A site-packages copy shadowing the watched tree: re-executing it would publish nothing real."""
    target = runtime.source_root / TARGET
    load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", load, file=str(tmp_path / "site-packages" / "sglang" / "forward_context.py"))
    queue(target)
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert load.calls == 0
    assert "does not match the watched source root path" in result["rejected"][0]["error"]


def test_a_module_with_no_file_is_refused(runtime: SimpleNamespace):
    """A module with no `__file__` cannot be shown to come from the watched tree.

    The forced dependent is registered so the only thing that can refuse this is the origin
    check itself, rather than a later "dependent is not loaded" error.
    """
    target = runtime.source_root / TARGET
    load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", load, file=None)
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, FakeLoad())
    queue(target)
    result = bootstrap.sync_pending()
    assert result["published"] == []
    assert load.calls == 0  # refused before the loader ran
    assert "does not match the watched source root path" in result["rejected"][0]["error"]


def test_a_matching_origin_publishes(runtime: SimpleNamespace):
    target = runtime.source_root / TARGET
    load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", load)
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, FakeLoad())
    queue(target)
    result = bootstrap.sync_pending()
    assert [item["path"] for item in result["published"]] == [TARGET]
    assert load.invalidations == 1 and load.calls == 1


def test_an_unresolved_symlinked_origin_still_matches(runtime: SimpleNamespace, tmp_path: Path):
    """`__file__` can be a pre-resolution path pointing at the same file through a symlink.

    Comparing the raw strings would reject a module that really is the watched one, so the
    origin check resolves both sides.
    """
    link = tmp_path / "link"
    link.symlink_to(runtime.source_root, target_is_directory=True)
    target = runtime.source_root / TARGET
    unresolved = link / TARGET
    assert str(unresolved) != str(target) and unresolved.resolve() == target.resolve()
    register(runtime, target, "sglang.srt.model_executor.forward_context", FakeLoad(), file=str(unresolved))
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, FakeLoad())
    queue(target)
    assert [item["path"] for item in bootstrap.sync_pending()["published"]] == [TARGET]


def test_published_records_the_forced_dependent_it_reexecuted(runtime: SimpleNamespace):
    target = runtime.source_root / TARGET
    dependent_load = FakeLoad()
    register(runtime, target, "sglang.srt.model_executor.forward_context", FakeLoad())
    register(runtime, runtime.source_root / "dep.py", DEPENDENT, dependent_load)
    queue(target)
    published = bootstrap.sync_pending()["published"][0]
    assert published["forced_dependents_reexecuted"] == [DEPENDENT]
    assert dependent_load.invalidations == 1 and dependent_load.calls == 1


# --- an uninstalled runtime publishes nothing ---


def test_sync_pending_is_inert_before_install(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bootstrap, "_INSTALLED", False)
    assert bootstrap.sync_pending() == {"installed": False, "published": [], "rejected": []}


def test_sync_pending_with_an_empty_queue_does_not_run_hooks(runtime: SimpleNamespace):
    assert bootstrap.sync_pending() == {"installed": True, "published": [], "rejected": []}
    assert runtime.hooks.pre_calls == 0 and runtime.hooks.post_calls == 0
