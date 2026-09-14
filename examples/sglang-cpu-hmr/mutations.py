"""Deterministic, byte-restoring source mutations for the CPU smoke.

Line-based on purpose: an AST round-trip would rewrite the whole file, so "exactly one
Python source changed" could no longer distinguish the one inserted line from incidental
reformatting of everything around it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

MARKER = "HMR_PROBE_SGLANG_CPU_PRINT_D410F975_001"


def print_statement(marker: str = MARKER) -> str:
    """The exact line the smoke inserts, and the exact prefix it later greps for.

    The PID is part of the printed text because the assertion is not just "the new code
    ran" but "the new code ran in the scheduler process that owns the loaded model".
    """
    if not marker.startswith("HMR_PROBE_SGLANG_") or not marker.replace("_", "").isalnum():
        raise ValueError("marker must be an alphanumeric HMR_PROBE_SGLANG_ token")
    return f'print("{marker}", "pid=" + str(__import__("os").getpid()), flush=True)'


def marker_lines(text: str, marker: str = MARKER) -> list[str]:
    return [line for line in text.splitlines() if marker in line]


@dataclass
class SavedFile:
    path: Path
    existed: bool
    content: bytes


class MutationSet:
    """Restore every edit byte-for-byte, including on assertion failure."""

    def __init__(self, source_root: str | Path):
        self.root = Path(source_root).resolve()
        self.saved: dict[Path, SavedFile] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.restore()

    def _save(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"path escapes source root: {relative}")
        if path not in self.saved:
            self.saved[path] = SavedFile(path, path.exists(), path.read_bytes() if path.exists() else b"")
        return path

    def insert_statement(self, relative: str, function: str, statement: str, *, class_name: str | None = None) -> int:
        """Insert one physical source line as the function's first statement.

        Refuses mixed indentation: if the source uses tabs, the inserted line will also use tabs;
        if it uses spaces, spaces. A file mixing both is rejected rather than producing broken output.
        """
        path = self._save(relative)
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        scope: ast.AST = tree
        if class_name is not None:
            classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
            if len(classes) != 1:
                raise AssertionError(f"expected one class {class_name!r} in {relative}, found {len(classes)}")
            scope = classes[0]
        candidates = scope.body if isinstance(scope, (ast.Module, ast.ClassDef)) else []
        matches = [node for node in candidates if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function]
        if len(matches) != 1:
            raise AssertionError(f"expected one function {function!r} in {relative}, found {len(matches)}")
        node = matches[0]
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            if first.end_lineno is None:
                raise AssertionError("AST node has no end_lineno")
            line = first.end_lineno + 1
        else:
            line = first.lineno
        source_lines = text.splitlines(keepends=True)
        # Copy the body's own leading whitespace rather than rebuilding it from `col_offset`: that
        # is the only way to reproduce tabs, and it needs no assumption about how wide a tab is.
        if first.lineno == node.lineno:
            raise ValueError(f"{function!r} in {relative} has its body on the `def` line; there is no body indentation to copy")
        body_line = source_lines[first.lineno - 1]
        indent = body_line[: len(body_line) - len(body_line.lstrip())]
        if " " in indent and "\t" in indent:
            raise ValueError(f"{function!r} in {relative} mixes tabs and spaces in its body indentation {indent!r}; refusing to guess")
        source_lines.insert(line - 1, f"{indent}{statement}\n")
        path.write_text("".join(source_lines), encoding="utf-8")
        return line

    def restore(self) -> None:
        for saved in self.saved.values():
            if saved.existed:
                saved.path.write_bytes(saved.content)
            elif saved.path.exists():
                saved.path.unlink()
        self.saved.clear()
