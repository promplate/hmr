import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent


@unittest.skipUnless(os.name == "posix", "POSIX daemon handoff")
class DaemonHandoffTests(unittest.TestCase):
    def test_explicit_reloader_stops_automatic_daemon(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").touch()
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    dedent("""\
                        import time
                        import hmr_daemon.posix.main as daemon
                        from reactivity.hmr.core import BaseReloader

                        # Wait for the automatic daemon to install its handoff hook.
                        deadline = time.monotonic() + 5
                        while not hasattr(daemon, "original_init"):
                            assert time.monotonic() < deadline, "daemon did not install its handoff hook"
                            time.sleep(0.01)

                        BaseReloader("main.py")
                        assert daemon.shutdown_event.wait(1), "automatic daemon remained active after explicit reloader construction"
                        """),
                ],
                cwd=directory,
                env={key: value for key, value in os.environ.items() if key != "NO_HMR_DAEMON"},
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
