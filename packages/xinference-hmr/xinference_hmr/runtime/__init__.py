"""Entry points invoked from the `sitecustomize` shim, and the role gate that scopes them.

Xinference spawns its sub-pools with `create_subprocess_exec` and an environment
inherited from the worker, so a `PYTHONPATH` shim reaches *every* process in the tree:
the REST/CLI process, the spawned supervisor+worker process, the multiprocessing
resource tracker, and the sub-pool that owns the model. Only the last one is in scope.

`is_model_pool_process` is that gate. It is deliberately a check on the interpreter's
own argv rather than on an environment variable: the environment is inherited and so
cannot distinguish these processes, while the sub-pool is the only one exec'd as
`python -m xoscar.backends.indigen start_sub_pool`. Note that the sub-pool renames
itself via setproctitle once it is up ("Model: <uid>"), so this check only works from
`site` time, before that rename -- which is also the only time early enough to matter.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SUBPOOL_MODULE = "xoscar.backends.indigen"
SUBPOOL_COMMAND = "start_sub_pool"


def is_model_pool_process(argv: list[str] | None = None) -> bool:
    """True only in a xoscar sub-pool interpreter, which is where a `ModelActor` is created.

    `sys.orig_argv` rather than `sys.argv`: by the time `site` runs, `sys.argv` for a
    `-m` launch has already been rewritten to drop the `-m <module>` pair, so the module
    name that identifies a sub-pool is only visible in the original argv.
    """
    args = sys.orig_argv if argv is None else argv
    return SUBPOOL_MODULE in args and SUBPOOL_COMMAND in args


def install() -> bool:
    """The default runtime entrypoint. Installs HMR only in a model-owning sub-pool.

    Returns whether anything was installed, so the shim's caller (and the tests) can tell
    "not this process" apart from "installed".
    """
    if not is_model_pool_process():
        return False

    from . import telemetry
    from .bootstrap import install_from_env

    install_from_env()
    telemetry.event("role_gate_passed", pid=os.getpid(), orig_argv=sys.orig_argv)
    _register_actor_patch()
    return True


def _register_actor_patch() -> None:
    """Patch `ModelActor` when its module is imported, not now.

    `xinference.core.model` is imported inside the sub-pool while xoscar deserialises the
    `create_actor` message, which is long after `site`. Importing it here instead would
    pull torch and the whole model stack into every sub-pool at interpreter start, and
    would do so before HMR's meta path hook is in place for the rest of the tree.
    """
    sys.meta_path.insert(0, _PostImportHook("xinference.core.model", _patch_actor))


def _patch_actor() -> None:
    from . import telemetry
    from .actor import install as install_actor
    from .actor import install_debug_endpoints

    verify_live_source_root()
    install_actor()
    install_debug_endpoints()
    telemetry.event("actor_boundary_installed", pid=os.getpid())


def verify_live_source_root() -> Path:
    """Confirm this process really imports Xinference from the tree the watcher is watching.

    The install runs at `site` time, when `xinference` is not yet imported, so the source root
    it was given can only be a guess made from install metadata. By the time
    `xinference.core.model` has executed, `xinference.__file__` is the ground truth -- and on
    this image the two disagree whenever the server was launched from a cwd other than the
    source tree, because that cwd is what `-m` puts on `sys.path[0]` (verified).

    Watching a tree this process did not import from is the one failure mode that looks exactly
    like success: the watcher sees the edit, publishes nothing (the path is not in the module
    map), and the only symptom is a marker that never appears. So this raises rather than
    warning, and it names both paths.
    """
    import xinference

    from . import telemetry
    from .bootstrap import manifest_source_root

    configured = manifest_source_root()
    if configured is None:  # not installed in this process; nothing to verify
        return Path(str(xinference.__file__)).parent.parent
    if xinference.__file__ is None:
        raise RuntimeError("xinference has no __file__, so the live source root cannot be verified")
    live = Path(xinference.__file__).resolve().parent.parent
    if live != configured:
        raise RuntimeError(
            f"HMR is watching {configured} but this process imported xinference from {live}. "
            f"An edit under the watched tree would never reach this process. On the official image "
            f"the model sub-pool inherits the server's cwd and `-m` puts it on sys.path[0], so "
            f"launch from {live} (or pass --hmr-source-root {live})."
        )
    telemetry.event("live_source_root_verified", pid=os.getpid(), source_root=str(live))
    return live


class _PostImportHook:
    """A `MetaPathFinder` that runs `callback` right after `module_name` finishes executing.

    Python has no post-import hook, and `importlib.util.LazyLoader`/`sys.modules` tricks
    both change *when* the module executes. This wraps the real loader's `exec_module`
    instead, so the module executes exactly as it would have, and the callback observes a
    fully initialised module.
    """

    def __init__(self, module_name: str, callback):
        self.module_name = module_name
        self.callback = callback
        self._finding = False

    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002 - importlib MetaPathFinder protocol signature
        if fullname != self.module_name or self._finding:
            return None
        self._finding = True  # re-entrancy guard: `find_spec` below walks `sys.meta_path`, including us
        try:
            from importlib.util import find_spec

            spec = find_spec(fullname)
        except (ImportError, AttributeError, ValueError):
            return None
        finally:
            self._finding = False
        if spec is None or spec.loader is None:
            return None

        callback = self.callback
        loader = spec.loader
        original_exec = loader.exec_module

        def exec_module(module):
            original_exec(module)
            callback()

        # Per-spec, not per-loader class: assigning to `loader.exec_module` on the instance
        # leaves every other module's loader untouched.
        loader.exec_module = exec_module
        return spec


def source_root_from_env() -> Path | None:
    raw = os.getenv("HMR_XINFERENCE_SOURCE_ROOT")
    return Path(raw) if raw else None
