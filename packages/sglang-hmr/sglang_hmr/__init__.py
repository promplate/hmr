"""HMR-aware wrapper for the official SGLang CLI.

`sglang-hmr serve MODEL ...` behaves like `sglang serve MODEL ...` with hot module
replacement enabled for one narrow, verified request path. Your SGLang argv is
forwarded unchanged and the official `sglang` entrypoint is `exec`'d, so the server
keeps this process's PID, signal handling, and exit code.

On `serve`, the wrapper installs early HMR injection via PYTHONPATH and environment
variables. No SGLang flags are ever injected or rewritten. No other subcommand is
touched.

The runtime itself lives in `sglang_hmr.runtime` and watches exactly two files
(see `sglang_hmr.runtime.scope`). It does not watch the whole SGLang tree, and it
fails fast when the source root or manifest does not match. Use `--hmr-disabled`
to opt out and get a plain `sglang` launch.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

SHIM_DIR = Path(__file__).resolve().parent / "_sitecustomize"
DEFAULT_RUNTIME = "sglang_hmr.runtime.bootstrap:install"

HELP = """\
Usage: sglang-hmr [--hmr-<option> ...] <sglang argv ...>

Runs the official `sglang` CLI via exec (same PID, signals, and exit code) with HMR
enabled by default for `serve`. Your SGLang arguments are forwarded unchanged.

  sglang-hmr serve facebook/opt-125m --device cpu --port 31000

Source root: an editable/source SGLang checkout is detected automatically. A
site-packages install cannot be edited in place, so it is an error there; pass
--hmr-source-root explicitly. Nothing is ever copied on your behalf.

HMR options (each also settable as an environment variable):
  --hmr-source-root PATH   HMR_SGLANG_SOURCE_ROOT   SGLang source tree to watch
  --hmr-manifest PATH      HMR_SGLANG_MANIFEST      JSON manifest of watched files
  --hmr-runtime SPEC       HMR_SGLANG_RUNTIME       override the default runtime entrypoint
  --hmr-disabled           HMR_SGLANG_DISABLED      plain `sglang` launch, no injection (any non-empty value)
                                                    also strips an activation already in the environment
  --hmr-print-env          print the computed environment and exec argv, then exit

Scope: with the packaged runtime, only `python/sglang/srt/model_executor/forward_context.py`
and its direct dependent `python/sglang/srt/model_executor/model_runner.py` are reloadable,
and the source root is checked for both. A `--hmr-manifest` must name exactly that set,
neither wider nor narrower. A `--hmr-runtime` override owns its own scope, so only the
source root's existence is checked, but the spec itself must resolve to a callable here or
the launch fails. Evidence covers SGLang 0.5.16 on the official CPU image, CPU device,
single scheduler, one request path.

Not implemented: model weight, scheduler, entrypoint, config, and CUDA/Triton kernel hot
replacement; multi-rank atomic publication. See README.md.
"""

HMR_OPTIONS = {
    "--hmr-source-root": "HMR_SGLANG_SOURCE_ROOT",
    "--hmr-runtime": "HMR_SGLANG_RUNTIME",
    "--hmr-manifest": "HMR_SGLANG_MANIFEST",
}

HMR_FLAGS = {"--hmr-disabled": "HMR_SGLANG_DISABLED"}


class UsageError(Exception):
    pass


def is_disabled(options: dict[str, str], base: dict[str, str]) -> bool:
    return bool(options.get("HMR_SGLANG_DISABLED") or base.get("HMR_SGLANG_DISABLED"))


def split_argv(argv: list[str]) -> tuple[dict[str, str], bool, list[str]]:
    """Split our own `--hmr-*` options out of the user's SGLang argv."""
    options: dict[str, str] = {}
    print_env = False
    forwarded: list[str] = []
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--":
            forwarded.append(arg)
            forwarded.extend(rest)
            break
        if arg == "--hmr-print-env":
            print_env = True
            continue
        if arg in HMR_FLAGS:
            options[HMR_FLAGS[arg]] = "1"
            continue
        name, _, inline = arg.partition("=")
        if name in HMR_OPTIONS:
            if inline or "=" in arg:
                value = inline
            elif rest:
                value = rest.pop(0)
            else:
                raise UsageError(f"{name} requires a value")
            if not value:
                raise UsageError(f"{name} requires a non-empty value")
            options[HMR_OPTIONS[name]] = value
        elif name.startswith("--hmr-"):
            raise UsageError(f"unknown option {name}")
        else:
            forwarded.append(arg)
    return options, print_env, forwarded


def is_serve(forwarded: list[str]) -> bool:
    """`serve` is the only subcommand whose runtime needs injection, and it must come first.

    SGLang's root CLI (`sglang.cli.main`) is `add_subparsers(dest="subcommand", required=True)`
    with no options of its own, so the subcommand is always `argv[0]`. Skipping leading
    `-`-prefixed tokens to hunt for a positional would be inventing a syntax SGLang rejects,
    and would enable HMR for an argv that never reaches `serve`.
    """
    return forwarded[:1] == ["serve"]


def deactivate(env: dict[str, str], *, owned: dict[str, str] | None = None) -> dict[str, str]:
    """Undo an activation this wrapper may have inherited, and drop what it owns itself.

    `owned` are the variables this invocation derived from `--hmr-*` options. On a non-serve
    subcommand nothing is injected, so those must not reach the child either: `sglang version`
    would otherwise run with `HMR_SGLANG_RUNTIME` set purely because we parsed the flag.
    """
    env.pop("HMR_SGLANG_ENABLE", None)
    for key in owned or {}:
        env.pop(key, None)
    if "PYTHONPATH" in env:
        remaining = os.pathsep.join(entry for entry in env["PYTHONPATH"].split(os.pathsep) if entry != str(SHIM_DIR))
        env["PYTHONPATH"] = remaining
        if not remaining:
            del env["PYTHONPATH"]
    return env


def build_env(options: dict[str, str], base: dict[str, str], *, serve: bool) -> dict[str, str]:
    """Compute the exec environment. CLI options win over inherited variables."""
    env = dict(base)
    env.update(options)
    env.pop("HMR_SGLANG_DISABLED", None)
    if is_disabled(options, base) or not serve:
        return deactivate(env, owned=options)
    runtime = env.setdefault("HMR_SGLANG_RUNTIME", DEFAULT_RUNTIME)
    from .shim import check_runtime

    try:
        check_runtime(runtime)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    from .source import SourceRootError, resolve_source_root

    try:
        resolved = resolve_source_root(env.get("HMR_SGLANG_SOURCE_ROOT"))
    except SourceRootError as exc:
        raise UsageError(f"{exc}\nPass --hmr-disabled to launch SGLang without HMR.") from None
    if runtime == DEFAULT_RUNTIME:
        from .runtime.scope import ScopeError, load_manifest, validate_source_root

        try:
            resolved = validate_source_root(resolved)
            if manifest := env.get("HMR_SGLANG_MANIFEST"):
                load_manifest(Path(manifest), resolved)
        except ScopeError as exc:
            raise UsageError(str(exc)) from None
    elif not resolved.is_dir():
        raise UsageError(f"source root {resolved} does not exist")
    env["HMR_SGLANG_SOURCE_ROOT"] = str(resolved)
    env["HMR_SGLANG_ENABLE"] = "1"
    shim = str(SHIM_DIR)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = shim if not existing else f"{shim}{os.pathsep}{existing}" if shim not in existing.split(os.pathsep) else existing
    return env


def resolve_sglang(which: Callable[[str], str | None] | None = None) -> str:
    path = (which or shutil.which)("sglang")
    if path is None:
        raise UsageError("`sglang` was not found on PATH; install SGLang first (this wrapper never bundles it)")
    return path


def build_exec(argv: list[str], base: dict[str, str], which: Callable[[str], str | None] | None = None) -> tuple[str, list[str], dict[str, str], bool]:
    """Resolve everything needed for `execve` without touching the process."""
    options, print_env, forwarded = split_argv(argv)
    serve = is_serve(forwarded)
    executable = resolve_sglang(which)
    env = build_env(options, base, serve=serve)
    return executable, ["sglang", *forwarded], env, print_env


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in {"-h", "--help"}:
        print(HELP, end="")
        raise SystemExit(0 if args else 2)
    try:
        executable, exec_argv, env, print_env = build_exec(args, dict(os.environ))
    except UsageError as exc:
        print(f"sglang-hmr: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if print_env:
        for key in sorted(key for key in env if key.startswith("HMR_SGLANG_") or key == "PYTHONPATH"):
            print(f"{key}={env[key]}")
        print(" ".join([executable, *exec_argv[1:]]))
        raise SystemExit(0)
    os.execve(executable, exec_argv, env)


if __name__ == "__main__":
    main()
