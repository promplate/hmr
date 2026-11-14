"""Resolve the Xinference source tree whose files will be watched and re-executed.

Two things make this less obvious for Xinference than for a plain library.

First, the official CPU image installs from a local directory (`pip install /opt/inference`),
which produces a *regular* site-packages install whose source tree still exists on disk at
`/opt/inference`. Both copies are importable, and on a fresh image they are byte-identical,
so nothing about a running server reveals which one it is using. `direct_url.json` is read
to find the source tree, since a directory install leaves no `__editable__` finder behind.

Second, and the reason the two copies must not be confused: **which copy the model process
imports depends on the cwd the server was launched from** (verified on this image). xoscar
starts a sub-pool as `python -m xoscar.backends.indigen ...`, inheriting the worker's cwd,
and `-m` puts that cwd on `sys.path[0]` once `runpy` takes over. Launch from `/opt/inference`
and the sub-pool imports `xinference` from the source tree; launch from anywhere else and it
falls through to site-packages, while the REST process -- a console script -- imports from
site-packages either way.

So this resolution is a *starting guess*, never a conclusion. `xinference_hmr.runtime`
re-resolves against `xinference.__file__` inside the sub-pool itself and refuses to install
against a tree that process did not import from, because editing the wrong copy is a change
no running process will ever load, and that mistake is invisible until a marker never appears.
"""

from __future__ import annotations

import json
from pathlib import Path


class SourceRootError(RuntimeError):
    """No Xinference source tree could be resolved, and none was given explicitly."""


def _candidate_root(package_file: Path) -> Path | None:
    """The parent of a `xinference` package directory, when it looks like a source tree."""
    candidate = package_file.parent.parent
    if (candidate / "xinference").is_dir() and (candidate / "xinference" / "__init__.py").is_file():
        return candidate
    return None


def find_source_root_via_import() -> Path | None:
    """If `import xinference` already resolves to a source tree (not site-packages), return its root."""
    try:
        import xinference
    except (ImportError, OSError, RuntimeError):
        # Importing xinference pulls in pydantic and a good deal else, so a partially installed
        # environment raises from deep inside that chain rather than as a top-level `ImportError`.
        return None
    if xinference.__file__ is None:
        return None
    package_file = Path(xinference.__file__)
    if "site-packages" in package_file.parts or "dist-packages" in package_file.parts:
        return None
    return _candidate_root(package_file)


def find_source_root_via_direct_url() -> Path | None:
    """Read `direct_url.json` for an install made from a local directory.

    This is how the official CPU image is built, and the resulting site-packages copy carries
    no `__editable__` finder -- the only on-disk record of where the source lives is this file.
    """
    try:
        from importlib.metadata import distribution

        dist = distribution("xinference")
        raw = dist.read_text("direct_url.json")
    except Exception:
        # `PackageNotFoundError`, and anything the metadata backend raises for a broken install.
        return None
    if not raw:
        return None
    try:
        url = json.loads(raw).get("url")
    except ValueError:
        return None
    if not isinstance(url, str) or not url.startswith("file://"):
        return None  # a VCS or archive URL is not a directory we can watch
    candidate = Path(url.removeprefix("file://"))
    if not candidate.is_dir():
        return None
    return _candidate_root(candidate / "xinference" / "__init__.py")


def resolve_source_root(explicit: str | None) -> Path:
    """Resolve the source root: explicit CLI value, then import, then install metadata."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    for finder in (find_source_root_via_import, find_source_root_via_direct_url):
        if detected := finder():
            return detected.resolve()
    raise SourceRootError(
        "no Xinference source tree was found (`import xinference` resolves to an installed "
        "package and its install metadata names no local directory). An installed package "
        "cannot be edited in place, and this wrapper will not copy source or weights on your "
        "behalf: pass --hmr-source-root pointing at an Xinference source tree."
    )
