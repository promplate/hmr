"""Early `ReactiveModule` injection inside the BentoML service worker, published at a request boundary."""

import ast
import atexit
import contextlib
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

from watchfiles import watch

from .scope import CLASS, FUNCTION, TARGET, load_manifest, sha256, shape
from .transaction import _load_handle, _restore_module_state, _snapshot_module_state

_LOCK = threading.RLock()
_STOP = threading.Event()
_PENDING = None
_ACTIVE = 0
_THREAD = None
_MANIFEST = None
_BASELINE = b""
_ACCEPTED = ""


def event(kind, **data):
    record = {"kind": kind, "pid": os.getpid(), "thread": threading.current_thread().name, "t": time.monotonic(), **data}
    print("BENTOML_HMR " + json.dumps(record), flush=True)


def _stop():
    _STOP.set()
    if _THREAD is not None:
        _THREAD.join(timeout=5)


def _watch():
    global _PENDING
    assert _MANIFEST is not None
    path = _MANIFEST.path_for(TARGET)
    for changes in watch(path.parent, stop_event=_STOP, debounce=100, step=20):
        if not any(Path(raw).resolve() == path for _, raw in changes):
            continue
        try:
            digest = sha256(path)
        except Exception:
            continue
        with _LOCK:
            if digest == _ACCEPTED:
                continue
            _PENDING = digest
            event("source_change", path=TARGET, sha256=digest, active=_ACTIVE)
            if _ACTIVE:
                event("publication_deferred", reason="active_worker_request", sha256=digest)


def _contract(module):
    """The published function is only accepted if it still parses a request body into its model.

    Only this declared contract is verified; a candidate's other side effects are not undone by the
    namespace rollback below.
    """
    from pydantic import BaseModel

    class Probe(BaseModel):
        value: int

    # `api_endpoint` reaches the implementation through this dict, so a reload that rebuilds the class
    # but leaves `ALL_SERDE` pointing at the previous one would publish nothing.
    published = module.ALL_SERDE["application/json"]
    assert published is getattr(module, CLASS), f"ALL_SERDE['application/json'] is {published!r}, not the reloaded {CLASS}"
    payload = module.Payload((b'{"value": 7}',), metadata={})
    with contextlib.redirect_stdout(io.StringIO()):  # a candidate marker must not reach the log from here
        assert published().deserialize_model(payload, Probe).value == 7


def sync_pending():
    global _PENDING, _ACCEPTED
    if _PENDING is None:
        return
    assert _MANIFEST is not None
    from reactivity.hmr.core import HMR_CONTEXT, get_path_module_map

    target = _MANIFEST.path_for(TARGET)
    digest = _PENDING
    snapshots = []
    try:
        source = target.read_bytes()
        # syntax_preflight before shape: compile-rejected code (e.g. module-level `return 1`)
        # parses to valid AST so shape() succeeds, then load() compiles and raises SyntaxError
        # into sys.excepthook, returning normally — leaving old code while reporting published.
        try:
            compile(source, str(target), "exec", dont_inherit=True)
        except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
            raise ValueError(f"syntax_preflight: {type(exc).__name__}: {exc}") from None
        if shape(source) != shape(_BASELINE):
            raise ValueError(f"only the body of {CLASS}.{FUNCTION} may change")
        module = get_path_module_map()[target]
        if module.__file__ is None or Path(module.__file__).resolve() != target:
            raise ValueError(f"live origin mismatch: {module.__file__} is not {target}")
        snapshots.append((module, _snapshot_module_state(module)))
        previous = id(module.__dict__[CLASS].__dict__[FUNCTION])
        with HMR_CONTEXT.batch():
            try:
                load = _load_handle(module)
                load.invalidate()
                load()
                _contract(module)
                if sha256(target) != digest:
                    raise ValueError("candidate changed during publication")
            except Exception:
                for reverting, snapshot in snapshots:
                    _restore_module_state(reverting, snapshot)
                raise
        current = id(module.__dict__[CLASS].__dict__[FUNCTION])
        _ACCEPTED = digest
        _PENDING = None
        event("published", path=TARGET, sha256=digest, function=f"{CLASS}.{FUNCTION}", previous_function_id=previous, function_id=current, active=_ACTIVE)
    except Exception as exc:
        # A batch flush re-marks the module dirty; after a rollback that flag must go back too, or the
        # next request reloads the same broken candidate instead of keeping the old implementation.
        for reverting, snapshot in snapshots:
            _load_handle(reverting).dirty = snapshot.dirty
        event("rejected", path=TARGET, sha256=digest, error=f"{type(exc).__name__}: {exc}", retryable=True)


def _install_boundary():
    """Wrap the per-request entry point on the class, before `to_asgi` binds it into a route.

    `ServiceAppFactory.routes` captures `functools.partial(self.api_endpoint_wrapper, name)`, so the
    bound method is frozen at app construction; patching the class afterwards would never be reached.
    """
    from _bentoml_impl.server.app import ServiceAppFactory

    original = ServiceAppFactory.api_endpoint_wrapper

    async def api_endpoint_wrapper(self, name, request):
        global _ACTIVE
        with _LOCK:
            if not _ACTIVE:
                sync_pending()
            _ACTIVE += 1
            event("request_begin", api=name, active=_ACTIVE)
        try:
            return await original(self, name, request)
        finally:
            with _LOCK:
                _ACTIVE -= 1
                event("request_end", api=name, active=_ACTIVE)

    ServiceAppFactory.api_endpoint_wrapper = api_endpoint_wrapper
    return original


def prepare(manifest_path):
    """Run inside the worker command line, before the worker module imports any BentoML source."""
    global _MANIFEST, _BASELINE, _ACCEPTED, _THREAD

    from reactivity.hmr.core import patch_meta_path

    imported = sorted(name for name in sys.modules if name.split(".")[0] in ("bentoml", "_bentoml_impl", "_bentoml_sdk"))
    if imported:
        raise RuntimeError(f"HMR must be installed before BentoML imports, found {imported}")
    _MANIFEST = load_manifest(Path(manifest_path))
    target = _MANIFEST.path_for(TARGET)
    _BASELINE = target.read_bytes()
    _ACCEPTED = sha256(target)
    ast.parse(_BASELINE)  # a baseline this runtime cannot parse would make every candidate shape-compare fail open
    # This hmr build's `patch_meta_path` returns nothing; it installs the finder at `sys.meta_path[0]`.
    patch_meta_path(includes=[str(_MANIFEST.path_for(path)) for path in _MANIFEST.reactive_paths])
    original = _install_boundary()
    # `app.py` imports serde only inside `api_endpoint`, so nothing has put it in `sys.modules` yet.
    # Importing it here, under the finder installed above, is what makes it the reactive module that
    # every later per-request `from ..serde import ALL_SERDE` resolves against.
    import _bentoml_impl.serde
    from reactivity.hmr.core import ReactiveModule

    module = sys.modules["_bentoml_impl.serde"]
    if not isinstance(module, ReactiveModule):
        raise RuntimeError(f"{TARGET} was not loaded as a ReactiveModule: {type(module)}")
    if Path(_bentoml_impl.serde.__file__).resolve() != target:
        raise RuntimeError(f"imported serde from {_bentoml_impl.serde.__file__}, expected {target}")
    _THREAD = threading.Thread(target=_watch, name="bentoml-hmr-watch", daemon=True)
    _THREAD.start()
    atexit.register(_stop)
    event("worker_installed", watcher_alive=_THREAD.is_alive(), reactive_module=str(module.__file__), wrapped=original.__qualname__, manifest=_MANIFEST.as_dict())
