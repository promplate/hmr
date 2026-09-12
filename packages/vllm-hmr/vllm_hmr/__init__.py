"""HMR-aware wrapper for the official vLLM CLI.

`vllm-hmr serve MODEL ...` behaves like `vllm serve MODEL ...` with hot module
replacement enabled for one narrow, verified request path. Your vLLM argv is
forwarded unchanged and the official `vllm` entrypoint is `exec`'d, so the server
keeps this process's PID, signal handling, and exit code.

On `serve`, the wrapper appends the two vLLM flags its runtime needs
(`--middleware` and `--worker-extension-cls`), unless you already passed them.
No other subcommand is touched, and no flag is ever injected twice.

The runtime itself lives in `vllm_hmr.runtime` and watches exactly two files
(see `vllm_hmr.runtime.scope`). It does not watch the whole vLLM tree, and it
fails fast when the source root or manifest does not match. Use `--hmr-disabled`
to opt out and get a plain `vllm` launch.
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

DEFAULT_RUNTIME = "vllm_hmr.runtime.bootstrap:install_unless_registry_inspector"
MIDDLEWARE = "vllm_hmr.runtime.middleware.HMRBoundaryMiddleware"
WORKER_EXTENSION = "vllm_hmr.runtime.worker.HMRWorkerExtension"

HELP = """\
Usage: vllm-hmr [--hmr-<option> ...] <vllm argv ...>

Runs the official `vllm` CLI via exec (same PID, signals, and exit code) with HMR
enabled by default for `serve`. Your vLLM arguments are forwarded unchanged.

  vllm-hmr serve facebook/opt-125m --port 8000

On `serve`, these vLLM flags are appended unless you passed them yourself:
  --middleware            vllm_hmr.runtime.middleware.HMRBoundaryMiddleware
  --worker-extension-cls  vllm_hmr.runtime.worker.HMRWorkerExtension

Source root: an editable/source vLLM checkout is detected automatically. A
site-packages install cannot be edited in place, so it is an error there; pass
--hmr-source-root explicitly. Nothing is ever copied on your behalf.

HMR options (each also settable as an environment variable):
  --hmr-source-root PATH   HMR_VLLM_SOURCE_ROOT   vLLM source tree to watch
  --hmr-manifest PATH      HMR_VLLM_MANIFEST      JSON manifest of watched files
  --hmr-runtime SPEC       HMR_VLLM_RUNTIME       override the default runtime entrypoint
  --hmr-disabled           HMR_VLLM_DISABLED      plain `vllm` launch, no injection (any non-empty value)
                                                  also strips an activation already in the environment
  --hmr-print-env          print the computed environment and exec argv, then exit

Scope: with the packaged runtime, only `vllm/renderers/inputs/preprocess.py` and
its direct dependent `vllm/v1/engine/async_llm.py` are reloadable, and the source
root is checked for both. A `--hmr-manifest` must name exactly that set, neither
wider nor narrower. A `--hmr-runtime` override owns its own scope, so only the
source root's existence is checked, but the spec itself must resolve to a callable
here or the launch fails. Evidence covers vLLM 0.28.0+cpu on the official CPU
image, single process, one request path.

Not implemented: model weight, compiled kernel, CUDA graph, scheduler, and
config hot replacement; multi-rank atomic publication. See README.md.
"""

# argv flag -> environment variable.
HMR_OPTIONS = {
    "--hmr-source-root": "HMR_VLLM_SOURCE_ROOT",
    "--hmr-runtime": "HMR_VLLM_RUNTIME",
    "--hmr-manifest": "HMR_VLLM_MANIFEST",
}

HMR_FLAGS = {"--hmr-disabled": "HMR_VLLM_DISABLED"}


class UsageError(Exception):
    pass


def is_disabled(options: dict[str, str], base: dict[str, str]) -> bool:
    """One rule for opting out, shared by flag injection and the exec environment.

    Any non-empty value counts, so `HMR_VLLM_DISABLED=0` cannot mean "disabled" for
    one half of the wrapper and "enabled" for the other: that combination used to
    install HMR without the middleware that publishes, i.e. a watcher that queues
    changes forever.
    """
    return bool(options.get("HMR_VLLM_DISABLED") or base.get("HMR_VLLM_DISABLED"))


def split_argv(argv: list[str]) -> tuple[dict[str, str], bool, list[str]]:
    """Split our own `--hmr-*` options out of the user's vLLM argv.

    Once we see `--`, everything after it (including further `--hmr-*` tokens)
    is forwarded to vLLM unchanged, since `--` terminates option parsing.
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


def is_serve(forwarded: list[str]) -> bool:
    """`serve` is the only subcommand whose runtime needs vLLM flags injected."""
    for arg in forwarded:
        if arg == "--":
            return False
        if not arg.startswith("-"):
            return arg == "serve"
    return False


def has_flag(forwarded: list[str], flag: str) -> bool:
    """True when the user already passed `flag`, in either `--x v` or `--x=v` form."""
    stop = forwarded.index("--") if "--" in forwarded else len(forwarded)
    return any(arg == flag or arg.startswith(f"{flag}=") for arg in forwarded[:stop])


def inject_vllm_flags(forwarded: list[str]) -> list[str]:
    """Append the two flags the runtime needs, without duplicating the user's own.

    Both are real vLLM 0.28 options (`--middleware` in `entrypoints/openai/cli_args.py`,
    `--worker-extension-cls` in `engine/arg_utils.py`). If the user already named one,
    theirs wins and ours is dropped: vLLM would otherwise see a conflicting value, and
    silently overriding a user's middleware chain is worse than not reloading.
    """
    if "--" in forwarded:  # everything after `--` is vLLM's business; append before it
        head, tail = forwarded[: forwarded.index("--")], forwarded[forwarded.index("--") :]
    else:
        head, tail = list(forwarded), []
    extra: list[str] = []
    if not has_flag(forwarded, "--middleware"):
        extra += ["--middleware", MIDDLEWARE]
    if not has_flag(forwarded, "--worker-extension-cls"):
        extra += ["--worker-extension-cls", WORKER_EXTENSION]
    return [*head, *extra, *tail]


def deactivate(env: dict[str, str]) -> dict[str, str]:
    """Undo an activation this wrapper may have inherited, and nothing else.

    An environment that already went through `build_env` once — `run.sh`, a nested launch, an
    exported shell profile — arrives carrying `HMR_VLLM_ENABLE=1` and our shim on `PYTHONPATH`.
    Dropping only `HMR_VLLM_DISABLED` then left both in place, so `--hmr-disabled` and every
    non-`serve` subcommand still installed HMR in the exec'd interpreter.

    Only the two things we inject are removed: the gate `sitecustomize` reads, and our own
    `PYTHONPATH` entry. The user's other entries stay, and so do the documented `HMR_VLLM_*`
    knobs, which are inert without the gate.
    """
    env.pop("HMR_VLLM_ENABLE", None)
    if "PYTHONPATH" in env:
        # Exact-match filtering, so a user entry that merely contains our path is untouched. An
        # empty result means the shim was the only entry, i.e. we put it there: drop the variable.
        remaining = os.pathsep.join(entry for entry in env["PYTHONPATH"].split(os.pathsep) if entry != str(SHIM_DIR))
        env["PYTHONPATH"] = remaining
        if not remaining:
            del env["PYTHONPATH"]
    return env


def build_env(options: dict[str, str], base: dict[str, str], *, serve: bool) -> dict[str, str]:
    """Compute the exec environment. CLI options win over inherited variables.

    HMR is on by default for `serve`: the runtime defaults to this package's own
    narrow-scope entrypoint and the source root is resolved here, in the wrapper,
    so a mistake is a clear CLI error rather than a failure inside vLLM's startup.
    """
    env = dict(base)
    env.update(options)
    env.pop("HMR_VLLM_DISABLED", None)
    if is_disabled(options, base) or not serve:
        # Opting out means the child sees a plain environment, not one carrying our marker.
        return deactivate(env)
    runtime = env.setdefault("HMR_VLLM_RUNTIME", DEFAULT_RUNTIME)
    from .shim import check_runtime

    try:
        check_runtime(runtime)  # `site` only prints a line when `sitecustomize` fails, so an unloadable spec has to fail here or it silently disables HMR
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    from .source import SourceRootError, resolve_source_root

    try:
        resolved = resolve_source_root(env.get("HMR_VLLM_SOURCE_ROOT"))
    except SourceRootError as exc:
        raise UsageError(f"{exc}\nPass --hmr-disabled to launch vLLM without HMR.") from None
    if runtime == DEFAULT_RUNTIME:
        from .runtime.scope import ScopeError, validate_source_root

        try:
            resolved = validate_source_root(resolved)  # only the packaged runtime owns this two-file scope
        except ScopeError as exc:
            raise UsageError(str(exc)) from None
    elif not resolved.is_dir():
        raise UsageError(f"source root {resolved} does not exist")
    env["HMR_VLLM_SOURCE_ROOT"] = str(resolved)
    env["HMR_VLLM_ENABLE"] = "1"
    shim = str(SHIM_DIR)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = shim if not existing else f"{shim}{os.pathsep}{existing}" if shim not in existing.split(os.pathsep) else existing
    return env


def resolve_vllm(which: Callable[[str], str | None] | None = None) -> str:
    # Resolved at call time, not at import time, so tests and wrappers can substitute the lookup.
    path = (which or shutil.which)("vllm")
    if path is None:
        raise UsageError("`vllm` was not found on PATH; install vLLM first (this wrapper never bundles it)")
    return path


def build_exec(argv: list[str], base: dict[str, str], which: Callable[[str], str | None] | None = None) -> tuple[str, list[str], dict[str, str], bool]:
    """Resolve everything needed for `execve` without touching the process."""
    options, print_env, forwarded = split_argv(argv)
    serve = is_serve(forwarded)
    if serve and not is_disabled(options, base):
        forwarded = inject_vllm_flags(forwarded)
    executable = resolve_vllm(which)
    env = build_env(options, base, serve=serve)
    return executable, ["vllm", *forwarded], env, print_env


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in {"-h", "--help"}:
        print(HELP, end="")
        raise SystemExit(0 if args else 2)
    try:
        executable, exec_argv, env, print_env = build_exec(args, dict(os.environ))
    except UsageError as exc:
        print(f"vllm-hmr: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if print_env:
        for key in sorted(key for key in env if key.startswith("HMR_VLLM_") or key == "PYTHONPATH"):
            print(f"{key}={env[key]}")
        print(" ".join([executable, *exec_argv[1:]]))
        raise SystemExit(0)
    os.execve(executable, exec_argv, env)


if __name__ == "__main__":
    main()
