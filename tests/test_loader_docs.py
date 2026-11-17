"""Executable checks for the HMR loader documentation (run with unittest)."""

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


class LoaderDocsTests(unittest.TestCase):
    def run_case(self, files, code):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, text in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            result = subprocess.run([sys.executable, "-c", code], cwd=root, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reload_hooks_and_disposal(self):
        self.run_case(
            {
                "main.py": """from reactivity.hmr import on_dispose
from pathlib import Path
with Path("events").open("a") as f: f.write("load\\n")
@on_dispose
def cleanup():
    with Path("events").open("a") as f: f.write("dispose\\n")
value = 1
"""
            },
            """
from pathlib import Path
from reactivity.hmr.api import SyncReloaderAPI
from reactivity.hmr.hooks import pre_reload, post_reload
@pre_reload
def before():
    with Path('events').open('a') as f: f.write('pre\\n')
@post_reload
def after():
    with Path('events').open('a') as f: f.write('post\\n')
r = SyncReloaderAPI(str(Path('main.py').resolve()))
r.run_with_hooks()
assert Path('events').read_text().splitlines() == ['pre', 'load', 'post']
p = Path('main.py').resolve()
p.write_text(p.read_text().replace('value = 1', 'value = 2'))
r.on_changes({p})
assert r.entry_module.value == 2
assert Path('events').read_text().splitlines() == ['pre', 'load', 'post', 'pre', 'dispose', 'load', 'post']
r.clean_up()
assert Path('events').read_text().splitlines()[-1] == 'post', 'cleanup is not a final module-dispose hook'
""",
        )

    def test_file_read_dependency(self):
        self.run_case(
            {"main.py": 'from pathlib import Path\nvalue = Path("data.txt").read_text()\n', "data.txt": "old"},
            """
from pathlib import Path
from reactivity.hmr.api import SyncReloaderAPI
r = SyncReloaderAPI(str(Path('main.py').resolve()))
r.run_with_hooks()
assert r.entry_module.value == 'old'
p = Path('data.txt').resolve()
p.write_text('new')
r.on_changes({p})
assert r.entry_module.value == 'new'
r.clean_up()
""",
        )

    def test_sync_and_async_contexts(self):
        self.run_case(
            {"main.py": "value = 1\n"},
            """
import asyncio
import sys
from reactivity.hmr.api import SyncReloaderAPI, AsyncReloaderAPI
original = sys.modules['__main__']
for cls in (SyncReloaderAPI, AsyncReloaderAPI):
    r = cls('main.py')
    with r as entered:
        assert isinstance(entered, super)
        assert r.entry_module.value == 1
    assert sys.modules['__main__'] is original
async def check():
    for cls in (SyncReloaderAPI, AsyncReloaderAPI):
        r = cls('main.py')
        async with r as entered:
            assert isinstance(entered, super)
            assert r.entry_module.value == 1
        assert sys.modules['__main__'] is original
asyncio.run(check())
""",
        )

    def test_hook_names_replace(self):
        self.run_case(
            {},
            """
from reactivity.hmr.hooks import pre_reload, call_pre_reload_hooks, use_pre_reload
calls = []
@pre_reload
def same(): calls.append('first')
@pre_reload
def same(): calls.append('second')
call_pre_reload_hooks()
assert calls == ['second']
with use_pre_reload(same): pass
call_pre_reload_hooks()
assert calls == ['second']
""",
        )

    def test_cli_module_and_real_file_watcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample").mkdir()
            (root / "sample/__init__.py").write_text("")
            dep = root / "sample/value.py"
            dep.write_text("value = 1\n")
            (root / "sample/__main__.py").write_text("from .value import value\nimport sys\nprint(value, __name__, __package__, sys.argv[1:], flush=True)\n")
            output = root / "output"
            with output.open("w") as log:
                process = subprocess.Popen([sys.executable, "-m", "reactivity.hmr", "-m", "sample", "hello"], cwd=root, stdout=log, stderr=log)
                try:
                    self.wait_for(output, "1 __main__ sample")
                    time.sleep(0.4)
                    dep.write_text("value = 2\n")
                    self.wait_for(output, "2 __main__ sample")
                    self.assertIn("['hello']", output.read_text())
                finally:
                    process.terminate()
                    process.wait(timeout=5)

    def wait_for(self, path, text):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            if text in path.read_text():
                return
            time.sleep(0.05)
        self.fail(path.read_text())


if __name__ == "__main__":
    unittest.main()
