"""Shim contract, including the `sitecustomize` chain executed by a real interpreter.

The chain only matters because CPython's `site` runs it, so the chaining tests launch real
subprocesses with a real `PYTHONPATH`. Asserting on `chain_to_next_sitecustomize` in-process
would not show that `site` imports our shim, nor that a shadowed `sitecustomize` still runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from sglang_hmr import SHIM_DIR
from sglang_hmr.shim import SKIP_MARKER, check_runtime, install_from_env, load_runtime, next_sitecustomize, parse_runtime, should_install

if TYPE_CHECKING:
    from pathlib import Path

REPORT = "import json, os, sys; print(json.dumps({'chained': os.environ.get('CHAINED_BY'), 'shim_ran': os.environ.get('SHIM_RAN'), 'enabled': os.environ.get('HMR_SGLANG_ENABLE')}))"


def run_python(env: dict[str, str], code: str = REPORT) -> dict[str, object]:
    """A real interpreter, so `site` really imports `sitecustomize` from `PYTHONPATH`."""
    result = subprocess.run([sys.executable, "-c", code], env={**env, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != 0:
        raise AssertionError(f"subprocess failed: {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def base_env(*path_entries: str) -> dict[str, str]:
    """Inherit only what the interpreter needs, so the parent's own HMR vars cannot leak in."""
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LD_LIBRARY_PATH", "VIRTUAL_ENV", "SYSTEMROOT"}}
    env["PYTHONPATH"] = os.pathsep.join(path_entries)
    return env


@pytest.fixture
def user_sitecustomize(tmp_path: Path) -> Path:
    """A `sitecustomize` the shim will shadow, which must still run."""
    directory = tmp_path / "user-site"
    directory.mkdir()
    (directory / "sitecustomize.py").write_text("import os\nos.environ['CHAINED_BY'] = 'user-sitecustomize'\n", encoding="utf-8")
    return directory


# --- the chain, in real subprocesses ---


def test_a_shadowed_sitecustomize_still_runs(user_sitecustomize: Path):
    """Our shim is first on `PYTHONPATH`, so without chaining the user's file would never run."""
    report = run_python(base_env(str(SHIM_DIR), str(user_sitecustomize)))
    assert report["chained"] == "user-sitecustomize"


def test_the_chain_runs_even_though_our_shim_is_imported_instead(user_sitecustomize: Path):
    """`sitecustomize` resolves to exactly one module, and on this path it is ours."""
    report = run_python(
        base_env(str(SHIM_DIR), str(user_sitecustomize)), "import sitecustomize, json, os; print(json.dumps({'file': sitecustomize.__file__, 'chained': os.environ.get('CHAINED_BY')}))"
    )
    assert report["file"] == str(SHIM_DIR / "sitecustomize.py")
    assert report["chained"] == "user-sitecustomize"


def test_the_shim_is_inert_without_the_enable_variable(user_sitecustomize: Path):
    """A plain launch must stay a plain launch: nothing is installed, but chaining still happens."""
    report = run_python(base_env(str(SHIM_DIR), str(user_sitecustomize)))
    assert report["enabled"] is None
    assert report["chained"] == "user-sitecustomize"


def test_the_shim_invokes_the_configured_runtime(tmp_path: Path):
    """End-to-end through `site`: the runtime callable really runs in a fresh interpreter."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "fake_runtime.py").write_text("import os\n\n\ndef install():\n    os.environ['SHIM_RAN'] = 'yes'\n", encoding="utf-8")
    env = base_env(str(SHIM_DIR), str(runtime_dir)) | {"HMR_SGLANG_ENABLE": "1", "HMR_SGLANG_RUNTIME": "fake_runtime:install"}
    report = run_python(env)
    assert report["shim_ran"] == "yes"


def test_the_shim_chains_before_installing(tmp_path: Path, user_sitecustomize: Path):
    """The user's `sitecustomize` may configure what the runtime needs, so it must run first."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "ordered_runtime.py").write_text("import os\n\n\ndef install():\n    os.environ['SHIM_RAN'] = os.environ.get('CHAINED_BY', 'chain-did-not-run-first')\n", encoding="utf-8")
    env = base_env(str(SHIM_DIR), str(user_sitecustomize), str(runtime_dir)) | {"HMR_SGLANG_ENABLE": "1", "HMR_SGLANG_RUNTIME": "ordered_runtime:install"}
    report = run_python(env)
    assert report["shim_ran"] == "user-sitecustomize"


def test_a_failing_runtime_is_not_silently_ignored(tmp_path: Path):
    """`site` swallows a `sitecustomize` traceback into one stderr line; it must still be visible."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "broken_runtime.py").write_text("def install():\n    raise RuntimeError('runtime refused to install')\n", encoding="utf-8")
    env = base_env(str(SHIM_DIR), str(runtime_dir)) | {"HMR_SGLANG_ENABLE": "1", "HMR_SGLANG_RUNTIME": "broken_runtime:install"}
    result = subprocess.run([sys.executable, "-c", "pass"], env={**env, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=60, check=False)
    assert "runtime refused to install" in result.stderr


def test_the_skip_marker_disables_installation_in_a_child(tmp_path: Path):
    """A subprocess that must not inherit HMR sets the marker rather than unsetting every variable."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "fake_runtime.py").write_text("import os\n\n\ndef install():\n    os.environ['SHIM_RAN'] = 'yes'\n", encoding="utf-8")
    env = base_env(str(SHIM_DIR), str(runtime_dir)) | {"HMR_SGLANG_ENABLE": "1", "HMR_SGLANG_RUNTIME": "fake_runtime:install", SKIP_MARKER: "1"}
    assert run_python(env)["shim_ran"] is None


def test_the_shim_works_with_no_other_sitecustomize_on_the_path():
    """`next_sitecustomize` returning None must not be an error."""
    assert run_python(base_env(str(SHIM_DIR)))["chained"] is None


def test_a_sitecustomize_package_is_chained_too(tmp_path: Path):
    """A `sitecustomize/` package shadows the same name as a module, so both forms must chain."""
    directory = tmp_path / "pkg-site"
    (directory / "sitecustomize").mkdir(parents=True)
    (directory / "sitecustomize" / "__init__.py").write_text("import os\nos.environ['CHAINED_BY'] = 'package-form'\n", encoding="utf-8")
    assert run_python(base_env(str(SHIM_DIR), str(directory)))["chained"] == "package-form"


# --- runtime spec parsing ---


def test_parse_runtime_splits_module_and_attribute():
    assert parse_runtime("pkg.mod:install") == ("pkg.mod", "install")


@pytest.mark.parametrize("spec", ["no_colon", ":install", "pkg.mod:", "", ":"])
def test_parse_runtime_rejects_a_malformed_spec(spec: str):
    """A typo must fail loudly here, not silently leave HMR disabled at runtime."""
    with pytest.raises(ValueError, match="must be 'module:callable'"):
        parse_runtime(spec)


def test_load_runtime_returns_the_callable():
    assert load_runtime("json:loads") is json.loads


def test_load_runtime_rejects_a_non_callable():
    with pytest.raises(TypeError, match="not callable"):
        load_runtime("json:__doc__")


def test_check_runtime_accepts_a_resolvable_spec():
    check_runtime("json:loads")


def test_check_runtime_reports_an_unimportable_module():
    with pytest.raises(ValueError, match="could not be loaded"):
        check_runtime("no_such_module_anywhere:install")


def test_check_runtime_reports_a_missing_attribute():
    with pytest.raises(ValueError, match="could not be loaded"):
        check_runtime("json:no_such_attribute")


def test_check_runtime_passes_a_malformed_spec_through_unchanged():
    """The two failures read differently, and the CLI surfaces both verbatim."""
    with pytest.raises(ValueError, match="must be 'module:callable'"):
        check_runtime("no_colon")


# --- should_install / install_from_env ---


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"HMR_SGLANG_ENABLE": "1", "HMR_SGLANG_RUNTIME": "pkg:install"}, True),
        ({"HMR_SGLANG_ENABLE": "0", "HMR_SGLANG_RUNTIME": "pkg:install"}, False),
        ({"HMR_SGLANG_ENABLE": "1"}, False),  # no runtime to call
        ({"HMR_SGLANG_ENABLE": "1", "HMR_SGLANG_RUNTIME": ""}, False),
        ({"HMR_SGLANG_RUNTIME": "pkg:install"}, False),
        ({"HMR_SGLANG_ENABLE": "1", "HMR_SGLANG_RUNTIME": "pkg:install", SKIP_MARKER: "1"}, False),
        ({}, False),
    ],
)
def test_should_install(env: dict[str, str], *, expected: bool):
    assert should_install(env) is expected


def test_install_from_env_does_nothing_when_not_enabled():
    assert install_from_env({}) is None


def test_install_from_env_calls_the_runtime(tmp_path: Path):
    """The runtime callable must be invoked; the return value is passed through."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "fake_runtime.py").write_text("import os\n\n\ndef install():\n    os.environ['TEST_SHIM_INSTALL_RAN'] = '1'\n    return 'runtime-result'\n", encoding="utf-8")
    original_path = sys.path[:]
    sys.path.insert(0, str(runtime_dir))
    try:
        result = install_from_env({"HMR_SGLANG_ENABLE": "1", "HMR_SGLANG_RUNTIME": "fake_runtime:install"})
        assert os.environ.pop("TEST_SHIM_INSTALL_RAN", None) == "1"
        assert result == "runtime-result"
    finally:
        sys.path[:] = original_path


# --- next_sitecustomize ---


def test_next_sitecustomize_skips_our_own_directory(tmp_path: Path):
    """Returning our own file would re-execute the shim and recurse."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text("", encoding="utf-8")
    assert next_sitecustomize(str(shim / "sitecustomize.py"), [str(shim)]) is None


def test_next_sitecustomize_finds_a_module_form(tmp_path: Path):
    shim, other = tmp_path / "shim", tmp_path / "other"
    shim.mkdir()
    other.mkdir()
    (shim / "sitecustomize.py").write_text("", encoding="utf-8")
    target = other / "sitecustomize.py"
    target.write_text("", encoding="utf-8")
    assert next_sitecustomize(str(shim / "sitecustomize.py"), [str(shim), str(other)]) == target


def test_next_sitecustomize_finds_a_package_form(tmp_path: Path):
    shim, other = tmp_path / "shim", tmp_path / "other"
    shim.mkdir()
    (other / "sitecustomize").mkdir(parents=True)
    (shim / "sitecustomize.py").write_text("", encoding="utf-8")
    target = other / "sitecustomize" / "__init__.py"
    target.write_text("", encoding="utf-8")
    assert next_sitecustomize(str(shim / "sitecustomize.py"), [str(shim), str(other)]) == target


def test_next_sitecustomize_ignores_empty_path_entries(tmp_path: Path):
    """An empty `sys.path` entry means the CWD, and `Path("")` would resolve to it silently."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text("", encoding="utf-8")
    assert next_sitecustomize(str(shim / "sitecustomize.py"), ["", str(shim)]) is None


def test_next_sitecustomize_returns_the_first_match(tmp_path: Path):
    shim, first, second = tmp_path / "shim", tmp_path / "first", tmp_path / "second"
    for directory in (shim, first, second):
        directory.mkdir()
        (directory / "sitecustomize.py").write_text("", encoding="utf-8")
    assert next_sitecustomize(str(shim / "sitecustomize.py"), [str(shim), str(first), str(second)]) == first / "sitecustomize.py"
