"""The one request path this package has real evidence for, plus manifest handling.

The default scope is deliberately two files. `vllm/renderers/inputs/preprocess.py`
is the provider that the CPU smoke actually hot-replaces; `vllm/v1/engine/async_llm.py`
is its real direct `from ... import` consumer, so it must be re-executed for the new
function object to reach the live request path. Widening this set is not a config
change: it needs new evidence that the target survives in-place replacement.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

TARGET = "vllm/renderers/inputs/preprocess.py"
DEPENDENT = "vllm.v1.engine.async_llm"
DEPENDENT_PATH = "vllm/v1/engine/async_llm.py"

# Both files are reactive (watched + re-executable); only TARGET is auto-published.
REACTIVE_PATHS = (TARGET, DEPENDENT_PATH)
MANIFEST_SCHEMA_VERSION = 1


class ScopeError(RuntimeError):
    """A source root or manifest that does not match what this runtime supports."""


@dataclass(frozen=True)
class Manifest:
    source_root: Path
    files: tuple[dict[str, str], ...]
    reactive_paths: tuple[str, ...]
    auto_paths: tuple[str, ...]
    forced_dependents: dict[str, tuple[str, ...]]

    def as_dict(self) -> dict:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "source_root": str(self.source_root),
            "files": [dict(item) for item in self.files],
            "reactive_paths": list(self.reactive_paths),
            "auto_paths": list(self.auto_paths),
            "forced_dependents": {key: list(value) for key, value in self.forced_dependents.items()},
        }

    def path_for(self, relative: str) -> Path:
        return self.source_root / relative

    def declared_scope(self) -> dict[str, object]:
        """Everything that decides what is watched, published, and re-executed; hashes are checked separately.

        Sorted, so manifest field order is not scope, and duplicates survive into the comparison
        rather than collapsing into a set that would accept a file listed twice.
        """
        return {
            "files": sorted(item.get("path", "") for item in self.files),
            "reactive_paths": sorted(self.reactive_paths),
            "auto_paths": sorted(self.auto_paths),
            "forced_dependents": {key: sorted(value) for key, value in self.forced_dependents.items()},
        }


def sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:  # `is_file()` passes for a file we cannot read; unhashable is as fatal as mismatched
        raise ScopeError(f"cannot hash {path}: {type(exc).__name__}: {exc}") from exc


def validate_source_root(root: Path) -> Path:
    """A source root is only usable when it actually contains the verified target."""
    resolved = root.expanduser().resolve()
    target = resolved / TARGET
    if not target.is_file():
        raise ScopeError(f"source root {resolved} does not contain {TARGET}; pass --hmr-source-root pointing at a vLLM source tree")
    missing = [relative for relative in REACTIVE_PATHS if not (resolved / relative).is_file()]
    if missing:
        raise ScopeError(f"source root {resolved} is missing files this runtime must watch: {', '.join(missing)}")
    return resolved


def build_manifest(root: Path) -> Manifest:
    source_root = validate_source_root(root)
    files = tuple({"path": relative, "sha256": sha256(source_root / relative)} for relative in REACTIVE_PATHS)
    return Manifest(source_root, files, REACTIVE_PATHS, (TARGET,), {TARGET: (DEPENDENT,)})


def _read_manifest(path: Path) -> dict:
    """Unreadable, non-JSON, and not-an-object are all "this manifest does not state a scope"."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ScopeError(f"cannot read manifest {path}: {type(exc).__name__}: {exc}") from exc
    except ValueError as exc:  # JSONDecodeError, and a UnicodeError from the decode
        raise ScopeError(f"manifest {path} is not valid JSON: {type(exc).__name__}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScopeError(f"manifest {path} must be a JSON object, got {type(raw).__name__}")
    return raw


def _string_list(path: Path, field: str, value: object) -> tuple[str, ...]:
    """A scope field is a list of strings or it is not a scope field.

    A bare string would otherwise iterate into characters and a non-iterable would raise
    `TypeError` out of the constructor, both of which reach the caller as something other
    than `ScopeError` — i.e. as a crash rather than as a rejected manifest.
    """
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ScopeError(f"manifest {path} field {field!r} must be a list of strings, got {value!r}")
    return tuple(value)


def _file_entries(path: Path, value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise ScopeError(f"manifest {path} field 'files' must be a list of objects, got {type(value).__name__}")
    entries = []
    for item in value:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("path", "sha256")):
            raise ScopeError(f"manifest {path} field 'files' entries must be objects with string 'path' and 'sha256', got {item!r}")
        entries.append({"path": item["path"], "sha256": item["sha256"]})
    return tuple(entries)


def _forced_dependents(path: Path, value: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise ScopeError(f"manifest {path} field 'forced_dependents' must be an object, got {type(value).__name__}")
    return {key: _string_list(path, f"forced_dependents[{key!r}]", names) for key, names in value.items()}


def load_manifest(path: Path, expected_root: Path | None = None) -> Manifest:
    raw = _read_manifest(path)
    if raw.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ScopeError(f"manifest {path} has schema_version {raw.get('schema_version')!r}, expected {MANIFEST_SCHEMA_VERSION}")
    # No defaults: an absent field is a manifest that does not state the scope, and filling one in
    # would be this loader inventing the scope the manifest exists to pin down.
    missing = sorted({"source_root", "files", "reactive_paths", "auto_paths", "forced_dependents"} - set(raw))
    if missing:
        raise ScopeError(f"manifest {path} is missing required fields: {', '.join(missing)}")
    if not isinstance(raw["source_root"], str):
        raise ScopeError(f"manifest {path} field 'source_root' must be a string, got {type(raw['source_root']).__name__}")
    try:
        source_root = Path(raw["source_root"]).resolve()
    except (OSError, ValueError) as exc:  # e.g. an embedded NUL, which `resolve()` rejects
        raise ScopeError(f"manifest {path} has an unusable source_root {raw['source_root']!r}: {type(exc).__name__}: {exc}") from exc
    if expected_root is not None and source_root != expected_root:
        raise ScopeError(f"manifest source_root {source_root} != configured source root {expected_root}")
    validate_source_root(source_root)
    manifest = Manifest(
        source_root,
        _file_entries(path, raw["files"]),
        _string_list(path, "reactive_paths", raw["reactive_paths"]),
        _string_list(path, "auto_paths", raw["auto_paths"]),
        _forced_dependents(path, raw["forced_dependents"]),
    )
    verify_manifest(manifest)
    return manifest


def verify_manifest(manifest: Manifest) -> None:
    """The scope is fixed, so a manifest must name it exactly, and its hashes must still match.

    Exactly, not "within": rejecting only additions let a manifest narrow the scope silently. An
    empty `files`, a `reactive_paths` without the dependent, or an `auto_paths` without the target
    all passed while installing a watcher that publishes nothing, or one that swaps the provider
    and leaves its consumer holding the old function object. Both are indistinguishable from
    working HMR until a request runs stale code.

    `build_manifest` is the one statement of that scope, so the comparison is against what it
    would produce for this same root rather than against a second copy of the constants.
    """
    expected = build_manifest(manifest.source_root).declared_scope()
    if manifest.declared_scope() != expected:
        raise ScopeError(f"manifest does not match this runtime's verified scope exactly: {manifest.declared_scope()} != {expected}")
    for item in manifest.files:
        path = manifest.path_for(item["path"])
        digest = sha256(path)
        if digest != item["sha256"]:
            raise ScopeError(f"manifest hash mismatch for {item['path']}: on disk {digest}, manifest {item['sha256']}")


def write_manifest(manifest: Manifest, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def syntax_preflight(path: Path) -> tuple[bool, str | None]:
    """Never hand a half-written file to the loader: a SyntaxError there is unrecoverable."""
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, None
