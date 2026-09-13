"""CLI contract: argv splitting, serve-only injection, env, execve, source mismatch."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from sglang_hmr import DEFAULT_RUNTIME, SHIM_DIR, UsageError, build_env, build_exec, deactivate, is_disabled, is_serve, main, resolve_sglang, split_argv
from sglang_hmr.runtime.scope import DEPENDENT, DEPENDENT_PATH, TARGET

FAKE_SGLANG = "/usr/bin/sglang"


def which_sglang(name: str) -> str | None:
    return FAKE_SGLANG if name == "sglang" else None


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    """A minimal tree that satisfies the packaged runtime's two-file scope."""
    for relative in (TARGET, DEPENDENT_PATH):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def test_split_argv_extracts_only_hmr_options():
    options, print_env, forwarded = split_argv(["--hmr-source-root", "/src", "serve", "m", "--port", "31000"])
    assert options == {"HMR_SGLANG_SOURCE_ROOT": "/src"}
    assert not print_env
    assert forwarded == ["serve", "m", "--port", "31000"]


def test_split_argv_accepts_inline_values():
    options, _, forwarded = split_argv(["--hmr-source-root=/src", "--hmr-manifest=/m.json", "serve", "m"])
    assert options == {"HMR_SGLANG_SOURCE_ROOT": "/src", "HMR_SGLANG_MANIFEST": "/m.json"}
    assert forwarded == ["serve", "m"]


def test_split_argv_stops_at_double_dash():
    """`--` terminates our parsing: a later `--hmr-*` token belongs to SGLang."""
    options, print_env, forwarded = split_argv(["serve", "m", "--", "--hmr-source-root", "/x", "--hmr-print-env"])
    assert options == {}
    assert not print_env
    assert forwarded == ["serve", "m", "--", "--hmr-source-root", "/x", "--hmr-print-env"]


@pytest.mark.parametrize("argv", [["--hmr-source-root"], ["--hmr-manifest"]])
def test_split_argv_rejects_missing_value(argv: list[str]):
    with pytest.raises(UsageError, match="requires a value"):
        split_argv(argv)


@pytest.mark.parametrize("argv", [["--hmr-source-root="], ["--hmr-manifest=", "serve"]])
def test_split_argv_rejects_empty_value(argv: list[str]):
    with pytest.raises(UsageError, match="requires a non-empty value"):
        split_argv(argv)


def test_split_argv_rejects_unknown_hmr_option():
    with pytest.raises(UsageError, match="unknown option --hmr-nope"):
        split_argv(["--hmr-nope", "serve"])


def test_split_argv_flags_and_print_env():
    options, print_env, forwarded = split_argv(["--hmr-disabled", "--hmr-print-env", "serve", "m"])
    assert options == {"HMR_SGLANG_DISABLED": "1"}
    assert print_env
    assert forwarded == ["serve", "m"]


@pytest.mark.parametrize(
    ("forwarded", "expected"),
    [
        (["serve", "m"], True),
        (["serve"], True),
        (["version"], False),
        (["generate", "m"], False),
        ([], False),
        (["--", "serve"], False),  # after `--` nothing is a subcommand of ours
        # SGLang's root parser has no options of its own and its subparsers are `required=True`,
        # so anything before the subcommand is an argv SGLang itself rejects. Treating it as
        # `serve` would activate HMR for a launch that never serves.
        (["--device", "cpu", "serve"], False),
        (["-x", "serve"], False),
        (["--port", "serve"], False),  # `serve` here is a flag's value, not the subcommand
    ],
)
def test_is_serve(forwarded: list[str], *, expected: bool):
    assert is_serve(forwarded) is expected


def test_is_serve_matches_the_real_sglang_root_parser():
    """`sglang.cli.main` accepts exactly these three subcommands, and only in first position."""
    assert is_serve(["serve"]) and not is_serve(["generate"]) and not is_serve(["version"])
    assert not is_serve(["Serve"]) and not is_serve(["serve=1"])


def test_build_env_enables_hmr_on_serve(source_root: Path):
    env = build_env({"HMR_SGLANG_SOURCE_ROOT": str(source_root)}, {}, serve=True)
    assert env["HMR_SGLANG_ENABLE"] == "1"
    assert env["HMR_SGLANG_RUNTIME"] == DEFAULT_RUNTIME
    assert env["HMR_SGLANG_SOURCE_ROOT"] == str(source_root)
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(SHIM_DIR)


def test_build_env_leaves_non_serve_untouched(source_root: Path):
    env = build_env({"HMR_SGLANG_SOURCE_ROOT": str(source_root)}, {}, serve=False)
    assert "HMR_SGLANG_ENABLE" not in env
    assert "PYTHONPATH" not in env


def test_non_serve_does_not_leak_wrapper_owned_variables(source_root: Path, tmp_path: Path):
    """Parsing `--hmr-*` must not configure a subcommand we deliberately do not inject into."""
    options = {"HMR_SGLANG_SOURCE_ROOT": str(source_root), "HMR_SGLANG_MANIFEST": str(tmp_path / "m.json"), "HMR_SGLANG_RUNTIME": "pkg:install"}
    env = build_env(options, {}, serve=False)
    assert not [key for key in env if key.startswith("HMR_SGLANG_")]


def test_non_serve_preserves_a_users_own_inherited_variable(source_root: Path):
    """Only what this invocation owns is dropped; the user's exported value is not ours to delete."""
    env = build_env({"HMR_SGLANG_RUNTIME": "pkg:install"}, {"HMR_SGLANG_SOURCE_ROOT": str(source_root)}, serve=False)
    assert "HMR_SGLANG_RUNTIME" not in env
    assert env["HMR_SGLANG_SOURCE_ROOT"] == str(source_root)


def test_disabled_serve_does_not_leak_wrapper_owned_variables(source_root: Path):
    env = build_env({"HMR_SGLANG_SOURCE_ROOT": str(source_root), "HMR_SGLANG_DISABLED": "1"}, {}, serve=True)
    assert "HMR_SGLANG_ENABLE" not in env
    assert "HMR_SGLANG_SOURCE_ROOT" not in env


def test_build_exec_does_not_enable_hmr_for_a_leading_option_argv(source_root: Path):
    """`sglang --device cpu serve` is not a valid SGLang argv, so it must not activate HMR."""
    _, exec_argv, env, _ = build_exec(["--hmr-source-root", str(source_root), "--device", "cpu", "serve"], {}, which_sglang)
    assert exec_argv == ["sglang", "--device", "cpu", "serve"]
    assert "HMR_SGLANG_ENABLE" not in env


def test_build_env_prepends_shim_preserving_user_pythonpath(source_root: Path):
    env = build_env({"HMR_SGLANG_SOURCE_ROOT": str(source_root)}, {"PYTHONPATH": "/user/lib"}, serve=True)
    assert env["PYTHONPATH"] == f"{SHIM_DIR}{os.pathsep}/user/lib"


def test_build_env_does_not_duplicate_the_shim(source_root: Path):
    base = {"PYTHONPATH": f"{SHIM_DIR}{os.pathsep}/user/lib"}
    env = build_env({"HMR_SGLANG_SOURCE_ROOT": str(source_root)}, base, serve=True)
    assert env["PYTHONPATH"] == base["PYTHONPATH"]


def test_disabled_strips_an_inherited_activation(source_root: Path):
    """A second launch inside an already-activated shell must really opt out."""
    base = {"HMR_SGLANG_ENABLE": "1", "HMR_SGLANG_SOURCE_ROOT": str(source_root), "PYTHONPATH": f"{SHIM_DIR}{os.pathsep}/user/lib"}
    env = build_env({"HMR_SGLANG_DISABLED": "1"}, base, serve=True)
    assert "HMR_SGLANG_ENABLE" not in env
    assert env["PYTHONPATH"] == "/user/lib"


def test_deactivate_drops_pythonpath_when_shim_was_the_only_entry():
    assert "PYTHONPATH" not in deactivate({"PYTHONPATH": str(SHIM_DIR)})


def test_deactivate_keeps_a_user_entry_that_merely_contains_the_shim_path():
    """Exact-match filtering: substring matching would eat a legitimate entry."""
    sibling = f"{SHIM_DIR}-other"
    assert deactivate({"PYTHONPATH": sibling})["PYTHONPATH"] == sibling


@pytest.mark.parametrize("value", ["1", "0", "false"])
def test_is_disabled_accepts_any_non_empty_value(value: str):
    """`HMR_SGLANG_DISABLED=0` must not mean "enabled" for half the wrapper."""
    assert is_disabled({}, {"HMR_SGLANG_DISABLED": value})


def test_is_disabled_ignores_empty_value():
    assert not is_disabled({}, {"HMR_SGLANG_DISABLED": ""})


def test_build_exec_forwards_sglang_argv_verbatim(source_root: Path):
    argv = ["--hmr-source-root", str(source_root), "serve", "facebook/opt-125m", "--device", "cpu", "--port", "31000"]
    executable, exec_argv, env, print_env = build_exec(argv, {}, which_sglang)
    assert executable == FAKE_SGLANG
    # argv[0] is the conventional name, and no `--hmr-*` token survives.
    assert exec_argv == ["sglang", "serve", "facebook/opt-125m", "--device", "cpu", "--port", "31000"]
    assert env["HMR_SGLANG_ENABLE"] == "1"
    assert not print_env


def test_build_exec_supports_model_path_form(source_root: Path):
    """`--model-path` is SGLang's own flag; the wrapper must not care which form is used."""
    argv = ["--hmr-source-root", str(source_root), "serve", "--model-path", "facebook/opt-125m"]
    _, exec_argv, env, _ = build_exec(argv, {}, which_sglang)
    assert exec_argv == ["sglang", "serve", "--model-path", "facebook/opt-125m"]
    assert env["HMR_SGLANG_ENABLE"] == "1"


def test_build_exec_injects_no_sglang_flags(source_root: Path):
    """SGLang has no middleware/worker-extension flags: the wrapper adds nothing to argv."""
    argv = ["--hmr-source-root", str(source_root), "serve", "m"]
    _, exec_argv, _, _ = build_exec(argv, {}, which_sglang)
    assert exec_argv[1:] == ["serve", "m"]


def test_build_exec_does_not_enable_hmr_for_other_subcommands(source_root: Path):
    _, exec_argv, env, _ = build_exec(["--hmr-source-root", str(source_root), "version"], {}, which_sglang)
    assert exec_argv == ["sglang", "version"]
    assert "HMR_SGLANG_ENABLE" not in env


def test_resolve_sglang_reports_a_missing_cli():
    with pytest.raises(UsageError, match="`sglang` was not found on PATH"):
        resolve_sglang(lambda _: None)


def test_build_env_rejects_a_site_packages_source_root(monkeypatch: pytest.MonkeyPatch):
    """A non-editable install has no source to edit: that must be a CLI error, not a copy."""
    monkeypatch.setattr("sglang_hmr.source.find_editable_sglang_root", lambda: None)
    with pytest.raises(UsageError, match="no editable SGLang source checkout"):
        build_env({}, {}, serve=True)


def test_build_env_rejects_a_source_root_without_the_target(tmp_path: Path):
    with pytest.raises(UsageError, match="does not contain"):
        build_env({"HMR_SGLANG_SOURCE_ROOT": str(tmp_path)}, {}, serve=True)


def test_build_env_rejects_a_source_root_missing_the_dependent(tmp_path: Path):
    target = tmp_path / TARGET
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(UsageError, match="missing files this runtime must watch"):
        build_env({"HMR_SGLANG_SOURCE_ROOT": str(tmp_path)}, {}, serve=True)


def test_build_env_rejects_an_unloadable_runtime(source_root: Path):
    """`site` reports a failing `sitecustomize` as one stderr line, so this must fail here."""
    with pytest.raises(UsageError, match="could not be loaded"):
        build_env({"HMR_SGLANG_SOURCE_ROOT": str(source_root), "HMR_SGLANG_RUNTIME": "no_such_module:install"}, {}, serve=True)


def test_build_env_rejects_a_malformed_runtime_spec(source_root: Path):
    with pytest.raises(UsageError, match="must be 'module:callable'"):
        build_env({"HMR_SGLANG_SOURCE_ROOT": str(source_root), "HMR_SGLANG_RUNTIME": "no_colon"}, {}, serve=True)


def test_build_env_verifies_a_passed_manifest(source_root: Path, tmp_path: Path):
    """A manifest first read inside `sitecustomize` fails as one stderr line, i.e. silently."""
    manifest = tmp_path / "bad.json"
    manifest.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(UsageError, match="schema_version"):
        build_env({"HMR_SGLANG_SOURCE_ROOT": str(source_root), "HMR_SGLANG_MANIFEST": str(manifest)}, {}, serve=True)


def test_build_env_accepts_a_matching_manifest(source_root: Path, tmp_path: Path):
    from sglang_hmr.runtime.scope import build_manifest, write_manifest

    manifest = write_manifest(build_manifest(source_root), tmp_path / "m.json")
    env = build_env({"HMR_SGLANG_SOURCE_ROOT": str(source_root), "HMR_SGLANG_MANIFEST": str(manifest)}, {}, serve=True)
    assert env["HMR_SGLANG_MANIFEST"] == str(manifest)


def test_custom_runtime_owns_its_own_scope(tmp_path: Path):
    """An override is not held to the packaged two-file scope, only to an existing root."""
    env = build_env({"HMR_SGLANG_SOURCE_ROOT": str(tmp_path), "HMR_SGLANG_RUNTIME": "json:loads"}, {}, serve=True)
    assert env["HMR_SGLANG_ENABLE"] == "1"


def test_custom_runtime_still_requires_the_root_to_exist(tmp_path: Path):
    missing = tmp_path / "nope"
    with pytest.raises(UsageError, match="does not exist"):
        build_env({"HMR_SGLANG_SOURCE_ROOT": str(missing), "HMR_SGLANG_RUNTIME": "json:loads"}, {}, serve=True)


def test_main_execve_receives_the_computed_environment(source_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """The CLI must hand over via execve, so the server keeps this PID and exit code."""
    calls: list[tuple[str, list[str], dict[str, str]]] = []
    monkeypatch.setattr(os, "execve", lambda path, argv, env: calls.append((path, argv, env)))
    monkeypatch.setattr("shutil.which", which_sglang)
    monkeypatch.setattr(os, "environ", {})
    main(["--hmr-source-root", str(source_root), "serve", "m"])
    (path, argv, env) = calls[0]
    assert path == FAKE_SGLANG
    assert argv == ["sglang", "serve", "m"]
    assert env["HMR_SGLANG_ENABLE"] == "1"
    assert not capsys.readouterr().out


def test_print_env_does_not_exec(source_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr(os, "execve", lambda *_: pytest.fail("execve must not run for --hmr-print-env"))
    monkeypatch.setattr("shutil.which", which_sglang)
    monkeypatch.setattr(os, "environ", {})
    with pytest.raises(SystemExit) as exit_info:
        main(["--hmr-print-env", "--hmr-source-root", str(source_root), "serve", "m"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "HMR_SGLANG_ENABLE=1" in out
    assert f"{FAKE_SGLANG} serve m" in out


def test_usage_error_exits_two(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr("shutil.which", which_sglang)
    monkeypatch.setattr(os, "environ", {})
    with pytest.raises(SystemExit) as exit_info:
        main(["--hmr-nope", "serve"])
    assert exit_info.value.code == 2
    assert "sglang-hmr: unknown option --hmr-nope" in capsys.readouterr().err


def test_no_argv_prints_help_and_exits_two(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2
    assert "Usage: sglang-hmr" in capsys.readouterr().out


def test_explicit_help_exits_zero(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    assert "Usage: sglang-hmr" in capsys.readouterr().out


def test_help_documents_the_exact_scope():
    from sglang_hmr import HELP

    assert TARGET in HELP
    assert DEPENDENT_PATH in HELP
    assert DEPENDENT not in HELP.replace(DEPENDENT_PATH, "")  # the module name is not what a user passes
