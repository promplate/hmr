"""HMR-aware wrapper for the official Xinference CLI.

`xinference-hmr local ...` behaves like `xinference-local ...` with hot module
replacement enabled inside the process that owns the loaded model. Your Xinference
argv is forwarded unchanged and the official entrypoint is `exec`'d, so the server
keeps this process's PID, signal handling, and exit code.

No Xinference flags are injected. The runtime is delivered entirely through
`PYTHONPATH` + `sitecustomize`, because the process that needs it is not this one:
xoscar spawns the model's sub-pool as a fresh interpreter, and only an inherited
environment reaches it. That same environment reaches the REST and supervisor
processes too, which is why `xinference_hmr.runtime.install` gates on the sub-pool's
own argv and installs nothing anywhere else.

The runtime watches exactly one file (see `xinference_hmr.runtime.scope`). It does not
watch the whole Xinference tree, and it fails fast when the source root or manifest
does not match. Use `--hmr-disabled` to opt out and get a plain launch.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

SHIM_DIR = Path(__file__).resolve().parent / "_sitecustomize"  # holds the `sitecustomize` shim; goes on PYTHONPATH of the exec'd interpreter, never on ours

DEFAULT_RUNTIME = "xinference_hmr.runtime:install"

# `xinference-hmr local ...` -> `xinference-local ...`. Anything else is passed to `xinference`
# as its own subcommand, so `xinference-hmr launch ...` still works like `xinference launch ...`.
SUBCOMMAND_EXECUTABLES = {"local": "xinference-local", "supervisor": "xinference-supervisor", "worker": "xinference-worker"}

HELP = """\
Usage: xinference-hmr [--hmr-<option> ...] <xinference argv ...>

Runs the official Xinference CLI via exec (same PID, signals, and exit code) with HMR
enabled in the sub-pool process that owns a loaded model. Your Xinference arguments are
forwarded unchanged, and no Xinference flag is ever injected.

  xinference-hmr local --host 0.0.0.0 --port 9997

The first non-flag argument selects the executable:
  local       -> xinference-local
  supervisor  -> xinference-supervisor
  worker      -> xinference-worker
  anything else stays a subcommand of `xinference` (e.g. `xinference-hmr launch ...`)

Source root: the tree the *sub-pool* imports from is what gets watched. Because xoscar
starts a sub-pool with `python -m` and the worker's cwd, that is the source tree, not
site-packages -- on the official CPU image, `/opt/inference`. It is detected from
`import xinference` and then from the install's `direct_url.json`. Nothing is ever copied
on your behalf.

HMR options (each also settable as an environment variable):
  --hmr-source-root PATH   HMR_XINFERENCE_SOURCE_ROOT   Xinference source tree to watch
  --hmr-manifest PATH      HMR_XINFERENCE_MANIFEST      JSON manifest of watched files
  --hmr-runtime SPEC       HMR_XINFERENCE_RUNTIME       override the default runtime entrypoint
  --hmr-disabled           HMR_XINFERENCE_DISABLED      plain launch, no injection (any non-empty value)
                                                        also strips an activation already in the environment
  --hmr-print-env          print the computed environment and exec argv, then exit

Scope: with the packaged runtime, only `xinference/model/llm/transformers/utils.py` is
reloadable. A `--hmr-manifest` must name exactly that set, neither wider nor narrower. A
`--hmr-runtime` override owns its own scope, so only the source root's existence is
checked, but the spec itself must resolve to a callable here or the launch fails.

Not implemented: model weight, tokenizer, `ModelActor` and model-class hot replacement;
vLLM/SGLang/MLX/llama.cpp backends; virtual-environment sub-pools (launch with
`enable_virtual_env=false`, or the sub-pool runs a different interpreter that never sees
this shim); multi-replica and multi-worker atomic publication. See README.md.
"""

# argv flag -> environment variable.
HMR_OPTIONS = {
    "--hmr-source-root": "HMR_XINFERENCE_SOURCE_ROOT",
    "--hmr-runtime": "HMR_XINFERENCE_RUNTIME",
    "--hmr-manifest": "HMR_XINFERENCE_MANIFEST",
}

HMR_FLAGS = {"--hmr-disabled": "HMR_XINFERENCE_DISABLED"}


class UsageError(Exception):
    pass


def is_disabled(options: dict[str, str], base: dict[str, str]) -> bool:
    """One rule for opting out, shared by the exec environment and the help text.

    Any non-empty value counts, so `HMR_XINFERENCE_DISABLED=0` cannot mean "disabled" for
    one half of the wrapper and "enabled" for the other.
    """
    return bool(options.get("HMR_XINFERENCE_DISABLED") or base.get("HMR_XINFERENCE_DISABLED"))


def split_argv(argv: list[str]) -> tuple[dict[str, str], bool, list[str]]:
    """Split our own `--hmr-*` options out of the user's Xinference argv.

    Once we see `--`, everything after it (including further `--hmr-*` tokens) is forwarded
    unchanged, since `--` terminates option parsing.
    """
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


def split_subcommand(forwarded: list[str]) -> tuple[str, list[str]]:
    """Map the leading subcommand to an executable, returning it and the argv to forward.

    `local`/`supervisor`/`worker` are separate console scripts rather than subcommands of
    `xinference`, so the token is consumed here. Everything else is a real `xinference`
    subcommand and stays in the forwarded argv.
    """
    for index, arg in enumerate(forwarded):
        if arg == "--":
            break
        if not arg.startswith("-"):
            if executable := SUBCOMMAND_EXECUTABLES.get(arg):
                return executable, [*forwarded[:index], *forwarded[index + 1 :]]
            return "xinference", list(forwarded)
    return "xinference", list(forwarded)


def _same_path(left: str | Path, right: str | Path) -> bool:
    """Compare path identity across aliases such as macOS `/var` and `/private/var`."""
    try:
        return Path(left).resolve() == Path(right).resolve()
    except (OSError, ValueError):
        return False


def deactivate(env: dict[str, str]) -> dict[str, str]:
    """Undo an activation this wrapper may have inherited, and nothing else.

    An environment that already went through `build_env` once -- a nested launch, an
    exported shell profile -- arrives carrying `HMR_XINFERENCE_ENABLE=1` and our shim on
    `PYTHONPATH`. Dropping only `HMR_XINFERENCE_DISABLED` would leave both in place, so
    `--hmr-disabled` would still install HMR in the exec'd interpreter.

    Only the two things we inject are removed: the gate `sitecustomize` reads, and our own
    `PYTHONPATH` entry. The user's other entries stay, and so do the documented
    `HMR_XINFERENCE_*` knobs, which are inert without the gate.
    """
    env.pop("HMR_XINFERENCE_ENABLE", None)
    if "PYTHONPATH" in env:
        # Exact-match filtering, so a user entry that merely contains our path is untouched. An
        # empty result means the shim was the only entry, i.e. we put it there: drop the variable.
        remaining = os.pathsep.join(entry for entry in env["PYTHONPATH"].split(os.pathsep) if not _same_path(entry, SHIM_DIR))
        env["PYTHONPATH"] = remaining
        if not remaining:
            del env["PYTHONPATH"]
    return env


def build_env(options: dict[str, str], base: dict[str, str]) -> dict[str, str]:
    """Compute the exec environment. CLI options win over inherited variables.

    The source root is resolved here, in the wrapper, so a mistake is a clear CLI error
    rather than a failure inside a sub-pool that only shows up as a marker never appearing.
    """
    env = dict(base)
    env.update(options)
    env.pop("HMR_XINFERENCE_DISABLED", None)
    if is_disabled(options, base):
        # Opting out means the child sees a plain environment, not one carrying our marker.
        return deactivate(env)
    runtime = env.setdefault("HMR_XINFERENCE_RUNTIME", DEFAULT_RUNTIME)
    from .shim import check_runtime

    try:
        check_runtime(runtime)  # `site` only prints a line when `sitecustomize` fails, so an unloadable spec has to fail here or it silently disables HMR
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    from .source import SourceRootError, resolve_source_root

    try:
        resolved = resolve_source_root(env.get("HMR_XINFERENCE_SOURCE_ROOT"))
    except SourceRootError as exc:
        raise UsageError(f"{exc}\nPass --hmr-disabled to launch Xinference without HMR.") from None
    if runtime == DEFAULT_RUNTIME:
        from .runtime.scope import ScopeError, load_manifest, validate_source_root

        try:
            resolved = validate_source_root(resolved)  # only the packaged runtime owns this one-file scope
            if manifest := env.get("HMR_XINFERENCE_MANIFEST"):
                load_manifest(Path(manifest), resolved)  # same reason as `check_runtime`: a manifest first read inside `sitecustomize` fails as one stderr line, i.e. as silently missing HMR
        except ScopeError as exc:
            raise UsageError(str(exc)) from None
    elif not resolved.is_dir():
        raise UsageError(f"source root {resolved} does not exist")
    env["HMR_XINFERENCE_SOURCE_ROOT"] = str(resolved)
    env["HMR_XINFERENCE_ENABLE"] = "1"
    shim = str(SHIM_DIR)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = shim if not existing else existing if any(_same_path(entry, SHIM_DIR) for entry in existing.split(os.pathsep)) else f"{shim}{os.pathsep}{existing}"
    return env


def resolve_executable(name: str, which: Callable[[str], str | None] | None = None) -> str:
    # Resolved at call time, not at import time, so tests and wrappers can substitute the lookup.
    path = (which or shutil.which)(name)
    if path is None:
        raise UsageError(f"`{name}` was not found on PATH; install Xinference first (this wrapper never bundles it)")
    return path


def build_exec(argv: list[str], base: dict[str, str], which: Callable[[str], str | None] | None = None) -> tuple[str, list[str], dict[str, str], bool]:
    """Resolve everything needed for `execve` without touching the process."""
    options, print_env, forwarded = split_argv(argv)
    name, forwarded = split_subcommand(forwarded)
    executable = resolve_executable(name, which)
    env = build_env(options, base)
    return executable, [name, *forwarded], env, print_env


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in {"-h", "--help"}:
        print(HELP, end="")
        raise SystemExit(0 if args else 2)
    try:
        executable, exec_argv, env, print_env = build_exec(args, dict(os.environ))
    except UsageError as exc:
        print(f"xinference-hmr: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if print_env:
        for key in sorted(key for key in env if key.startswith("HMR_XINFERENCE_") or key == "PYTHONPATH"):
            print(f"{key}={env[key]}")
        print(" ".join([executable, *exec_argv[1:]]))
        raise SystemExit(0)
    os.execve(executable, exec_argv, env)


if __name__ == "__main__":
    main()
