"""Manifest contract: exact scope, source root, hashes, schema, malformed input, UTF-8."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from sglang_hmr.runtime.scope import (
    DEPENDENT,
    DEPENDENT_PATH,
    MANIFEST_SCHEMA_VERSION,
    REACTIVE_PATHS,
    TARGET,
    ScopeError,
    build_manifest,
    load_manifest,
    sha256,
    syntax_preflight,
    validate_source_root,
    verify_manifest,
    write_manifest,
)

FIELDS = ("source_root", "files", "reactive_paths", "auto_paths", "forced_dependents")


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    for relative in REACTIVE_PATHS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_scope_is_exactly_the_verified_two_files():
    assert REACTIVE_PATHS == (TARGET, DEPENDENT_PATH)
    assert TARGET.endswith("model_executor/forward_context.py")
    assert DEPENDENT == "sglang.srt.model_executor.model_runner"


def test_build_manifest_pins_hashes(source_root: Path):
    manifest = build_manifest(source_root)
    assert manifest.source_root == source_root
    assert [item["path"] for item in manifest.files] == list(REACTIVE_PATHS)
    assert manifest.auto_paths == (TARGET,)  # only the provider is auto-published
    assert manifest.forced_dependents == {TARGET: (DEPENDENT,)}
    for item in manifest.files:
        assert item["sha256"] == sha256(source_root / item["path"])


def test_round_trip_through_disk(source_root: Path, tmp_path: Path):
    path = write_manifest(build_manifest(source_root), tmp_path / "m.json")
    assert load_manifest(path, source_root).as_dict() == build_manifest(source_root).as_dict()


def test_validate_source_root_rejects_a_tree_without_the_target(tmp_path: Path):
    with pytest.raises(ScopeError, match="does not contain"):
        validate_source_root(tmp_path)


def test_validate_source_root_rejects_a_tree_missing_the_dependent(tmp_path: Path):
    target = tmp_path / TARGET
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ScopeError, match="missing files this runtime must watch"):
        validate_source_root(tmp_path)


def test_hash_mismatch_is_rejected(source_root: Path, tmp_path: Path):
    path = write_manifest(build_manifest(source_root), tmp_path / "m.json")
    (source_root / TARGET).write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(ScopeError, match="manifest hash mismatch"):
        load_manifest(path, source_root)


def test_source_root_mismatch_is_rejected(source_root: Path, tmp_path: Path):
    path = write_manifest(build_manifest(source_root), tmp_path / "m.json")
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ScopeError, match="!= configured source root"):
        load_manifest(path, other)


@pytest.mark.parametrize("version", [None, 0, 2, "1", [1]])
def test_unknown_schema_version_is_rejected(source_root: Path, tmp_path: Path, version: object):
    payload = build_manifest(source_root).as_dict() | {"schema_version": version}
    with pytest.raises(ScopeError, match="schema_version"):
        load_manifest(write(tmp_path / "m.json", payload), source_root)


@pytest.mark.parametrize("version", [True, 1.0])
def test_a_version_that_merely_equals_one_is_rejected(source_root: Path, tmp_path: Path, version: object):
    """JSON `true` and `1.0` both `== 1`, but neither states the integer schema this runtime knows.

    Accepting them would let a generator that wrote the wrong type look like a pinned manifest.
    """
    payload = build_manifest(source_root).as_dict() | {"schema_version": version}
    with pytest.raises(ScopeError, match="schema_version"):
        load_manifest(write(tmp_path / "m.json", payload), source_root)


@pytest.mark.parametrize("field", FIELDS)
def test_a_missing_field_is_rejected(source_root: Path, tmp_path: Path, field: str):
    """No defaults: an absent field is a manifest that does not state the scope."""
    payload = build_manifest(source_root).as_dict()
    del payload[field]
    with pytest.raises(ScopeError, match="missing required fields"):
        load_manifest(write(tmp_path / "m.json", payload), source_root)


@pytest.mark.parametrize(
    ("field", "value"), [("source_root", 1), ("files", "x"), ("reactive_paths", "abc"), ("reactive_paths", [1]), ("auto_paths", {}), ("forced_dependents", []), ("forced_dependents", {TARGET: "x"})]
)
def test_a_wrongly_typed_field_is_rejected(source_root: Path, tmp_path: Path, field: str, value: object):
    """A bare string would iterate into characters; a non-iterable would crash the constructor."""
    payload = build_manifest(source_root).as_dict() | {field: value}
    with pytest.raises(ScopeError, match="must be"):
        load_manifest(write(tmp_path / "m.json", payload), source_root)


@pytest.mark.parametrize("entry", [{"path": TARGET}, {"path": TARGET, "sha256": 1}, "not-an-object"])
def test_a_malformed_file_entry_is_rejected(source_root: Path, tmp_path: Path, entry: object):
    payload = build_manifest(source_root).as_dict() | {"files": [entry]}
    with pytest.raises(ScopeError, match="'files'"):
        load_manifest(write(tmp_path / "m.json", payload), source_root)


def test_unparsable_json_is_rejected(source_root: Path, tmp_path: Path):
    path = tmp_path / "m.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ScopeError, match="is not valid JSON"):
        load_manifest(path, source_root)


def test_invalid_utf8_is_rejected_as_a_manifest_not_as_a_crash(source_root: Path, tmp_path: Path):
    """A `UnicodeDecodeError` must arrive as ScopeError, or it escapes as an unhandled crash."""
    path = tmp_path / "m.json"
    path.write_bytes(b'{"schema_version": 1, "source_root": "\xff\xfe"}')
    with pytest.raises(ScopeError, match="is not valid JSON"):
        load_manifest(path, source_root)


def test_a_non_object_manifest_is_rejected(source_root: Path, tmp_path: Path):
    with pytest.raises(ScopeError, match="must be a JSON object"):
        load_manifest(write(tmp_path / "m.json", [1, 2]), source_root)


def test_a_missing_manifest_file_is_rejected(source_root: Path, tmp_path: Path):
    with pytest.raises(ScopeError, match="cannot read manifest"):
        load_manifest(tmp_path / "absent.json", source_root)


def test_an_unusable_source_root_string_is_rejected(source_root: Path, tmp_path: Path):
    payload = build_manifest(source_root).as_dict() | {"source_root": "a\x00b"}
    with pytest.raises(ScopeError, match="unusable source_root"):
        load_manifest(write(tmp_path / "m.json", payload), source_root)


def test_a_widened_scope_is_rejected(source_root: Path, tmp_path: Path):
    """An extra reactive path would install a watcher over a file with no evidence."""
    payload = build_manifest(source_root).as_dict()
    payload["reactive_paths"] = [*payload["reactive_paths"], "python/sglang/srt/managers/scheduler.py"]
    with pytest.raises(ScopeError, match="does not match this runtime's verified scope exactly"):
        load_manifest(write(tmp_path / "m.json", payload), source_root)


def test_a_narrowed_reactive_scope_is_rejected(source_root: Path, tmp_path: Path):
    """Dropping the dependent swaps the provider and leaves its consumer on the old object."""
    payload = build_manifest(source_root).as_dict() | {"reactive_paths": [TARGET]}
    with pytest.raises(ScopeError, match="does not match this runtime's verified scope exactly"):
        load_manifest(write(tmp_path / "m.json", payload), source_root)


def test_an_empty_auto_scope_is_rejected(source_root: Path, tmp_path: Path):
    """A watcher that publishes nothing looks healthy while serving stale code forever."""
    payload = build_manifest(source_root).as_dict() | {"auto_paths": []}
    with pytest.raises(ScopeError, match="does not match this runtime's verified scope exactly"):
        load_manifest(write(tmp_path / "m.json", payload), source_root)


def test_an_empty_files_list_is_rejected(source_root: Path, tmp_path: Path):
    """With no files there is nothing to hash-check, i.e. no pinning at all."""
    payload = build_manifest(source_root).as_dict() | {"files": []}
    with pytest.raises(ScopeError, match="does not match this runtime's verified scope exactly"):
        load_manifest(write(tmp_path / "m.json", payload), source_root)


def test_a_dropped_forced_dependent_is_rejected(source_root: Path, tmp_path: Path):
    payload = build_manifest(source_root).as_dict() | {"forced_dependents": {}}
    with pytest.raises(ScopeError, match="does not match this runtime's verified scope exactly"):
        load_manifest(write(tmp_path / "m.json", payload), source_root)


def test_a_duplicate_file_entry_survives_into_the_comparison(source_root: Path, tmp_path: Path):
    """Sorted lists, not sets: a file listed twice must not collapse into a match."""
    payload = build_manifest(source_root).as_dict()
    payload["files"] = [*payload["files"], payload["files"][0]]
    with pytest.raises(ScopeError, match="does not match this runtime's verified scope exactly"):
        load_manifest(write(tmp_path / "m.json", payload), source_root)


def test_field_order_is_not_scope(source_root: Path, tmp_path: Path):
    payload = build_manifest(source_root).as_dict()
    payload["reactive_paths"] = list(reversed(payload["reactive_paths"]))
    path = write(tmp_path / "m.json", payload)
    loaded = load_manifest(path, source_root)
    assert loaded.reactive_paths == tuple(reversed(REACTIVE_PATHS))


def test_verify_manifest_accepts_a_freshly_built_one(source_root: Path):
    verify_manifest(build_manifest(source_root))


def test_sha256_of_an_unreadable_path_is_a_scope_error(source_root: Path):
    with pytest.raises(ScopeError, match="cannot hash"):
        sha256(source_root / "absent.py")


def test_syntax_preflight_accepts_valid_source(source_root: Path):
    assert syntax_preflight(source_root / TARGET) == (True, None)


def test_syntax_preflight_rejects_a_half_written_file(source_root: Path):
    """A SyntaxError inside the loader is unrecoverable, so it must be caught before publishing."""
    path = source_root / TARGET
    path.write_text("def broken(:\n", encoding="utf-8")
    ok, error = syntax_preflight(path)
    assert not ok
    assert error is not None and "SyntaxError" in error


def test_syntax_preflight_rejects_invalid_utf8(source_root: Path):
    path = source_root / TARGET
    path.write_bytes(b"x = '\xff\xfe'\n")
    ok, error = syntax_preflight(path)
    assert not ok
    assert error is not None and "Error" in error


def test_manifest_dict_is_json_round_trippable(source_root: Path):
    payload = build_manifest(source_root).as_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["schema_version"] == MANIFEST_SCHEMA_VERSION
