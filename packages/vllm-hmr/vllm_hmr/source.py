"""Detect whether the installed `vllm` package is editable (source checkout).

When vLLM is installed in editable mode (`pip install -e`), we can find its source
root automatically. When it's a regular site-packages install, there is no source
to edit, so we require an explicit `--hmr-source-root`.
"""

# vLLM is a runtime peer, not a build/type dependency of this package.
# pyright: reportMissingImports=false

from __future__ import annotations

from pathlib import Path


class SourceRootError(RuntimeError):
    """vLLM is not editable and no explicit source root was given."""


def find_editable_vllm_root() -> Path | None:
    """If `import vllm` resolves to an editable source checkout, return its root."""
    try:
        import vllm
    except ImportError:
        return None
    if vllm.__file__ is None:
        return None
    vllm_file = Path(vllm.__file__)
    # Editable installs point directly at source: .../vllm-source/vllm/__init__.py
    # Regular installs live under site-packages: .../site-packages/vllm/__init__.py
    if "site-packages" in vllm_file.parts or "dist-packages" in vllm_file.parts:
        return None
    # The parent of the `vllm` package directory is the source root.
    candidate = vllm_file.parent.parent
    if (candidate / "vllm").is_dir() and (candidate / "vllm" / "__init__.py").is_file():
        return candidate
    return None


def resolve_source_root(explicit: str | None) -> Path:
    """Resolve source root from explicit CLI or auto-detect editable vllm."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    detected = find_editable_vllm_root()
    if detected:
        return detected
    raise SourceRootError(
        "no editable vLLM source checkout was found (`import vllm` resolves to an "
        "installed package, or vLLM is not importable here). An installed package "
        "cannot be edited in place, and this wrapper will not copy source or weights "
        "on your behalf: pass --hmr-source-root pointing at a vLLM source tree."
    )
