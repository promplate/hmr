"""The one request path this package has evidence for, plus source-root handling.

The default scope is a single file: `ray/serve/_private/replica.py`. That module owns the
replica-side request path (`Replica.handle_request*` -> `_unpack_proxy_args` ->
`UserCallableWrapper.call_http_entrypoint`), so a change to it is a change to code that every
real request executes inside the actor that holds the model.

Two facts about Ray decide the mechanism, and both are checked here rather than assumed:

- `replica.py` is already imported before any deployment code runs (the actor class is defined
  in it), and `reactivity`'s `ReactiveModuleFinder` puts every site-packages directory in
  `excludes` unconditionally. So the import-hook route cannot reach this module at all; the
  module object has to be converted in place instead.
- The replica actor's own class is a `type(name, (ReplicaActor,), dict(ReplicaActor.__dict__))`
  subclass built by the controller and shipped over cloudpickle, so reloading cannot touch the
  actor class itself. It can touch `Replica`, because the actor builds `self._replica_impl`
  from the live module at init time.

Widening this set is not a config change: it needs evidence that the new target survives
in-place replacement on a live replica.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

TARGET_MODULE = "ray.serve._private.replica"
TARGET_PATH = "ray/serve/_private/replica.py"
REACTIVE_MODULES = (TARGET_MODULE,)


class ScopeError(RuntimeError):
    """A source root that does not hold the request path this runtime supports."""


@dataclass(frozen=True)
class Manifest:
    source_root: Path
    files: tuple[dict[str, str], ...]
    reactive_modules: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"source_root": str(self.source_root), "files": [dict(item) for item in self.files], "reactive_modules": list(self.reactive_modules)}

    def path_for(self, relative: str) -> Path:
        return self.source_root / relative


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_source_root(explicit: str | None = None) -> Path:
    """The tree whose `ray/serve/_private/replica.py` is the one the live process imported.

    Defaulting to the installed tree is deliberate and is why nothing is ever copied: the file
    that gets edited is the exact file the running interpreter loaded. An explicit root is still
    accepted, but it has to be the same file, otherwise edits would land somewhere the process
    never reads and a "no marker" result would be indistinguishable from a broken reload.
    """
    from ray.serve._private import replica

    live = Path(replica.__file__).resolve()
    installed_root = live.parents[3]  # .../ray/serve/_private/replica.py -> .../  (site-packages)
    root = Path(explicit).resolve() if explicit else installed_root
    target = root / TARGET_PATH
    if not target.is_file():
        raise ScopeError(f"source root {root} does not contain {TARGET_PATH}")
    if target.resolve() != live:
        raise ScopeError(f"source root {root} resolves {TARGET_PATH} to {target.resolve()}, but this process imported {live}")
    return root


def build_manifest(source_root: Path) -> Manifest:
    return Manifest(source_root, ({"path": TARGET_PATH, "sha256": sha256(source_root / TARGET_PATH)},), REACTIVE_MODULES)
