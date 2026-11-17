"""Real-process regression tests: python -m unittest discover -s tests -v."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

PACKAGE = Path(__file__).resolve().parents[1] / "packages" / "uvicorn-hmr"
APP = """
import asyncio
import builtins
import json
import os
import time
from pathlib import Path
from reactivity.hmr.fs import track

config = Path(__file__).with_name("config.txt").resolve()
track(config)
builtins.starts = getattr(builtins, "starts", 0)
builtins.active = getattr(builtins, "active", 0)
state = {}

async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        await receive()
        builtins.starts += 1
        builtins.active += 1
        assert builtins.active == 1, "overlapping lifespans"
        n = builtins.starts
        state.update(value=config.read_text(), pid=os.getpid(), starts=n)
        Path(f"started-{n}").write_text(str(os.getpid()))
        print("startup read", n, state, flush=True)
        while not Path(f"release-{n}").exists():
            if state["value"] == "blocking":
                time.sleep(0.01)
            else:
                await asyncio.sleep(0.01)
        print("startup released", n, flush=True)
        if state["value"] == "fail":
            await send({"type": "lifespan.startup.failed", "message": "expected test failure"})
            return
        await send({"type": "lifespan.startup.complete"})
        await receive()
        builtins.active -= 1
        await send({"type": "lifespan.shutdown.complete"})
    elif scope["type"] == "http":
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": json.dumps(state).encode()})
"""


class StartupWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "main.py").write_text(APP)
        (self.root / "config.txt").write_text("before")
        # Port 0 avoids port-allocation races; discover the actual listener in logs.
        self.log = (self.root / "server.log").open("w+")
        self.addCleanup(self.log.close)
        env = {**os.environ, "PYTHONPATH": str(PACKAGE)}
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn_hmr", "main:app", "--host", "127.0.0.1", "--port", "0"],
            cwd=self.root,
            env=env,
            stdout=self.log,
            stderr=self.log,
        )
        self.addCleanup(self.stop)

    def stop(self):
        if self.process.poll() is None:
            if sys.platform == "win32":
                # A venv launcher can have a separate interpreter child.
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], check=False, capture_output=True)
            else:
                self.process.terminate()
            # Uvicorn waits for startup to finish before graceful termination.
            for n in range(1, 20):
                (self.root / f"release-{n}").touch()
            try:
                self.process.wait(8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                self.fail("server did not exit after startup was released")
        self.log.flush()
        text = (self.root / "server.log").read_text()
        self.assertNotIn("overlapping lifespans", text)
        self.assertNotIn("Task was destroyed", text)
        self.assertNotIn("cancel scope", text)

    def wait(self, predicate):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if result := predicate():
                    return result
            except (URLError, OSError, ValueError, IndexError):
                pass
            if self.process.poll() is not None:
                break
            time.sleep(0.02)
        self.fail(f"{sorted(p.name for p in self.root.iterdir())}\n{(self.root / 'server.log').read_text()}")

    def started(self, n):
        self.wait(lambda: (self.root / f"started-{n}").exists())
        if os.environ.get("WATCHFILES_FORCE_POLLING"):
            time.sleep(1.1)  # Polling backends can have coarse timestamp resolution.

    def release(self, n):
        (self.root / f"release-{n}").touch()

    def response(self):
        text = (self.root / "server.log").read_text()
        # Each restart with port 0 can bind a different port.
        address = text.rsplit("Uvicorn running on ", 1)[1].split()[0]
        with urlopen(address, timeout=1) as response:
            return json.load(response)

    def value(self, value):
        def check():
            result = self.response()
            return result if result["value"] == value else None

        return self.wait(check)

    def test_initial_startup_and_later_reload(self):
        self.started(1)
        (self.root / "config.txt").write_text("during")
        time.sleep(0.4)
        self.assertFalse((self.root / "started-2").exists())
        self.release(1)
        self.started(2)
        self.release(2)
        result = self.value("during")
        self.assertEqual(result["pid"], int((self.root / "started-1").read_text()))
        (self.root / "config.txt").write_text("later")
        self.started(3)
        self.release(3)
        self.assertEqual(self.value("later")["pid"], result["pid"])

    def test_change_during_second_startup_and_atomic_replace(self):
        self.started(1)
        self.release(1)
        self.value("before")
        (self.root / "config.txt").write_text("second")
        self.started(2)
        replacement = self.root / "replacement.txt"
        replacement.write_text("third")
        replacement.replace(self.root / "config.txt")
        time.sleep(0.4)
        self.assertFalse((self.root / "started-3").exists())
        self.release(2)
        self.started(3)
        self.release(3)
        self.assertEqual(self.value("third")["pid"], int((self.root / "started-1").read_text()))

    def test_no_change_does_not_reload(self):
        self.started(1)
        self.release(1)
        self.value("before")
        (self.root / "untracked.txt").write_text("irrelevant")
        time.sleep(2)
        self.assertEqual(self.response()["starts"], 1)

    def test_blocking_startup_buffers_changes(self):
        self.started(1)
        self.release(1)
        self.value("before")
        (self.root / "config.txt").write_text("blocking")
        self.started(2)
        (self.root / "config.txt").write_text("after-blocking")
        time.sleep(0.4)
        self.assertFalse((self.root / "started-3").exists())
        self.release(2)
        self.started(3)
        self.release(3)
        self.value("after-blocking")

    @unittest.skipIf(sys.platform == "win32", "Windows terminate does not deliver SIGINT")
    def test_interrupt_during_startup_with_pending_change(self):
        self.started(1)
        (self.root / "config.txt").write_text("pending")
        time.sleep(0.4)
        self.process.send_signal(signal.SIGINT)
        self.release(1)
        self.process.wait(8)

    def test_failed_startup_with_pending_change_exits(self):
        self.started(1)
        # The first startup already read "before"; make the second startup fail.
        (self.root / "config.txt").write_text("fail")
        self.release(1)
        self.started(2)
        (self.root / "config.txt").write_text("pending")
        time.sleep(0.4)
        self.release(2)
        self.process.wait(8)
        self.assertFalse((self.root / "started-3").exists())


if __name__ == "__main__":
    unittest.main()
