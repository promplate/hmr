"""The probe must stay observability-only, and must not re-implement any runtime decision.

The receipt claims `hmr_sglang_probe` "installs no watcher, publishes nothing, and holds no
HMR state". That claim is what makes the smoke's evidence attributable to `sglang_hmr`, so it
is asserted here rather than trusted.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from hmr_sglang_probe import plugin, register_hooks

if TYPE_CHECKING:
    import pytest

PLUGIN_SOURCE = Path(plugin.__file__).read_text(encoding="utf-8")


class FakeParameter:
    def __init__(self, pointer: int):
        self._pointer = pointer

    def data_ptr(self) -> int:
        return self._pointer


class FakeModel:
    def parameters(self):
        yield FakeParameter(0xDEADBEEF)


class FakeResult:
    def __init__(self):
        self.internal_state: dict[str, Any] = {}


def fake_scheduler(model: object | None = None, weight_load_time: float = 1.5) -> SimpleNamespace:
    runner = SimpleNamespace(model=FakeModel() if model is None else model, weight_load_time=weight_load_time)
    return SimpleNamespace(tp_worker=SimpleNamespace(model_runner=runner))


# --- the probe must not duplicate runtime behaviour ---


def test_the_probe_never_publishes_or_watches():
    """Any of these names in the probe would mean it makes a reload decision of its own."""
    forbidden = (
        "sync_pending",
        "watch(",
        "watchfiles",
        "Change.",
        "call_pre_reload_hooks",
        "call_post_reload_hooks",
        "invalidate",
        "syntax_preflight",
        "load_manifest",
        "HMR_CONTEXT",
        "patch_meta_path",
        "install()",
    )
    present = [name for name in forbidden if name in PLUGIN_SOURCE]
    assert present == [], f"the probe must stay observability-only, but references {present}"


def test_the_probe_only_reads_hmr_state_through_the_packaged_runtime():
    """Its single `sglang_hmr` touchpoint is `state()`, which is a read."""
    tree = ast.parse(PLUGIN_SOURCE)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("sglang_hmr"):
            imported.extend(f"{node.module}:{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names if alias.name.startswith("sglang_hmr"))
    assert imported == ["sglang_hmr.runtime.bootstrap:state"]


def test_the_probe_reports_the_package_state_verbatim(monkeypatch: pytest.MonkeyPatch):
    """Patching the package's own `state`, not the plugin's wrapper: this is what proves the
    indirection really lands in `sglang_hmr` rather than in something the probe computed."""
    sentinel = {"installed": True, "pending": [], "manifest": None, "telemetry": {"events": []}}
    monkeypatch.setattr("sglang_hmr.runtime.bootstrap.state", lambda: sentinel)
    assert plugin._hmr_state() is sentinel  # noqa: SLF001 - the indirection to the package is the point


def test_the_probe_holds_no_module_level_mutable_state():
    """Module-level state would be HMR state the probe owns, which the receipt denies."""
    tree = ast.parse(PLUGIN_SOURCE)
    assigned = [target.id for node in tree.body if isinstance(node, ast.Assign) for target in node.targets if isinstance(target, ast.Name)]
    assert assigned == ["TRACKED_MODULES"]
    assert isinstance(plugin.TRACKED_MODULES, tuple)  # immutable, so it cannot accumulate anything


def test_registering_hooks_installs_exactly_one_read_only_after_hook(monkeypatch: pytest.MonkeyPatch):
    """One AFTER hook on an existing SGLang method is the whole instrumentation surface."""
    calls: list[tuple[str, object, object]] = []
    registry = SimpleNamespace(register=lambda target, hook, hook_type: calls.append((target, hook, hook_type)))
    hook_registry = SimpleNamespace(HookRegistry=registry, HookType=SimpleNamespace(AFTER="AFTER", BEFORE="BEFORE"))
    monkeypatch.setitem(sys.modules, "sglang", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "sglang.srt", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "sglang.srt.plugins", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "sglang.srt.plugins.hook_registry", hook_registry)
    register_hooks()
    assert len(calls) == 1
    target, hook, hook_type = calls[0]
    assert target == "sglang.srt.managers.scheduler.Scheduler.get_internal_state"
    assert hook is plugin.readback
    assert hook_type == "AFTER"  # BEFORE could change what SGLang does; AFTER only observes


# --- readback behaviour ---


def test_readback_returns_the_original_result_object(monkeypatch: pytest.MonkeyPatch):
    """Returning None would discard SGLang's own result, taking `/server_info` with it."""
    monkeypatch.setattr(plugin, "_hmr_state", lambda: {"installed": True})
    result = FakeResult()
    assert plugin.readback(result, fake_scheduler()) is result


def test_readback_reports_identity_a_weight_reload_would_change(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(plugin, "_hmr_state", lambda: {"installed": True})
    result = FakeResult()
    plugin.readback(result, fake_scheduler())
    probe = result.internal_state["hmr_probe"]
    assert probe["first_parameter_data_ptr"] == 0xDEADBEEF
    assert probe["weight_load_time"] == 1.5
    assert probe["pid"] > 0
    assert probe["hmr"] == {"installed": True}


def test_a_raising_probe_does_not_take_server_info_down(monkeypatch: pytest.MonkeyPatch):
    """`/server_info` is how the smoke observes everything; the probe must never break it."""
    monkeypatch.setattr(plugin, "_hmr_state", lambda: (_ for _ in ()).throw(RuntimeError("state exploded")))
    result = FakeResult()
    assert plugin.readback(result, fake_scheduler()) is result
    assert "hmr_probe" not in result.internal_state
    assert "state exploded" in result.internal_state["hmr_probe_error"]


def test_a_model_with_no_parameters_is_reported_as_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(plugin, "_hmr_state", dict)

    class Empty:
        def parameters(self):
            return iter(())

    result = FakeResult()
    plugin.readback(result, fake_scheduler(model=Empty()))
    assert result.internal_state["hmr_probe"]["first_parameter_data_ptr"] is None


def test_module_identity_does_not_import_an_absent_module(monkeypatch: pytest.MonkeyPatch):
    """Reading `sys.modules` directly: a module this process never imported must stay absent."""
    monkeypatch.delitem(sys.modules, "sglang.srt.model_executor.forward_context", raising=False)
    monkeypatch.delitem(sys.modules, "sglang.srt.model_executor.model_runner", raising=False)
    monkeypatch.delitem(sys.modules, "sglang.srt.managers.scheduler", raising=False)
    assert plugin._module_identity() == {}  # noqa: SLF001 - the private reader is the unit under test
    assert "sglang.srt.model_executor.forward_context" not in sys.modules


def test_module_identity_reports_live_function_object_ids(monkeypatch: pytest.MonkeyPatch):
    """`id()` of the live function is what an in-place swap changes, so it is what gets reported."""

    def has_forward_context():
        return True

    module = SimpleNamespace(__file__="/src/forward_context.py", has_forward_context=has_forward_context)
    monkeypatch.setitem(sys.modules, "sglang.srt.model_executor.forward_context", module)
    rows = plugin._module_identity()  # noqa: SLF001
    row = rows["sglang.srt.model_executor.forward_context"]
    assert row["module_file"] == "/src/forward_context.py"
    assert row["symbols"]["has_forward_context"] == id(has_forward_context)


def test_tracked_modules_covers_the_reactive_scope_and_the_scheduler():
    """The two reactive files plus the scheduler that owns the model."""
    assert plugin.TRACKED_MODULES == ("sglang.srt.model_executor.forward_context", "sglang.srt.model_executor.model_runner", "sglang.srt.managers.scheduler")
