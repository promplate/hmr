"""Detect whether the installed `sglang` package is editable (source checkout).

When SGLang is installed in editable mode (`pip install -e`), we can find its source
root automatically. When it's a regular site-packages install, there is no source
to edit, so we require an explicit `--hmr-source-root`.
"""

# SGLang is a runtime peer, not a build/type dependency of this package.
# pyright: reportMissingImports=false

from __future__ import annotations

from pathlib import Path


class SourceRootError(RuntimeError):
    """SGLang is not editable and no explicit source root was given."""


def find_editable_sglang_root() -> Path | None:
    """If `import sglang` resolves to an editable source checkout, return its root."""
    try:
        import sglang
    except ImportError:
        return None
    if sglang.__file__ is None:
        return None
    sglang_file = Path(sglang.__file__)
    # Editable installs point directly at source: .../sglang-source/python/sglang/__init__.py
    # Regular installs live under site-packages: .../site-packages/sglang/__init__.py
    if "site-packages" in sglang_file.parts or "dist-packages" in sglang_file.parts:
        return None
    # SGLang's layout has python/sglang, so the source root is two levels up from the package.
    candidate = sglang_file.parent.parent.parent
    if (candidate / "python" / "sglang" / "__init__.py").is_file():
        return candidate
    return None


def resolve_source_root(explicit: str | None) -> Path:
    """Resolve source root from explicit CLI or auto-detect editable sglang."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    detected = find_editable_sglang_root()
    if detected:
        return detected
    raise SourceRootError(
        "no editable SGLang source checkout was found (`import sglang` resolves to an "
        "installed package, or SGLang is not importable here). An installed package "
        "cannot be edited in place, and this wrapper will not copy source or weights "
        "on your behalf: pass --hmr-source-root pointing at a SGLang source tree."
    )
