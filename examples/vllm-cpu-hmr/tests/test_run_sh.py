"""Runs `run.sh` for real against a mock `docker` CLI.

This starts no container and builds no image, so it is evidence about the runner's own
staging, receipt and cleanup only, never about vLLM or HMR behaviour. What it does exercise
for real is bash: the trap ordering, `set -o pipefail` against `tee`, and the cid fallback.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
import unittest
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
RUN_SH = EXAMPLE_ROOT / "run.sh"
DOCKER_MOCK = Path(__file__).resolve().parent / "docker_mock.sh"
ARTIFACTS = ("cpu-smoke-receipt.json", "cpu-smoke-full.log", "cpu-source-manifest.json", "cpu-runner-full.log", "cpu-container.cid", "cpu-container-receipt.json")


@unittest.skipUnless(sys.platform.startswith("linux"), "run.sh requires Linux Bash, flock, and the official CPU Docker image")
class RunScriptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.results = self.root / "results"
        self.state = self.root / "docker-state"
        self.state.mkdir()
        # `docker` must resolve to the mock and to nothing else, so a missing case in the mock
        # surfaces as a failure here instead of silently reaching the real daemon.
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "docker").symlink_to(DOCKER_MOCK)
        for tool in ("bash", "flock", "tee", "date", "chmod", "dirname", "mkdir", "rm", "sleep", "kill"):
            if found := which_on_default_path(tool):
                (self.bin / tool).symlink_to(found)

    def env(self, **mock_env: str) -> dict[str, str]:
        return {
            "PATH": str(self.bin),
            "HOME": str(self.root),
            "VLLM_HMR_RESULTS": str(self.results),
            # A private lock: the real path would block this test behind an actual CPU run.
            "VLLM_HMR_LOCK": str(self.root / "lock"),
            "MOCK_STATE_DIR": str(self.state),
            **mock_env,
        }

    def run_script(self, cwd: Path | None = None, timeout: float = 60, **mock_env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(RUN_SH)], cwd=cwd or self.root, env=self.env(**mock_env), capture_output=True, text=True, timeout=timeout, check=False)

    def receipt(self) -> dict:
        return json.loads((self.results / "cpu-container-receipt.json").read_text())

    def containers(self) -> list[str]:
        return sorted(path.name for path in self.state.glob("container-*.json"))

    def test_normal_run_records_zero_exit_and_removed_container(self):
        completed = self.run_script()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = self.receipt()
        self.assertEqual(receipt["stage"], "run")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["run_exit_code"], 0)
        self.assertTrue(receipt["container_created"])
        self.assertTrue(receipt["container_removed"])
        self.assertFalse(receipt["cleanup_failed"])
        self.assertEqual(receipt["container_id"], (self.results / "cpu-container.cid").read_text().strip())
        self.assertEqual(self.containers(), [])

    def test_failed_build_writes_receipt_beside_the_log(self):
        completed = self.run_script(MOCK_BUILD_EXIT="7")
        # `tee` exits 0 on a failed build, so an unpropagated status would show up as 0 here.
        self.assertEqual(completed.returncode, 7, completed.stderr)
        log = self.results / "cpu-runner-full.log"
        self.assertIn("mock docker build", log.read_text())
        receipt = self.receipt()
        self.assertEqual(receipt["stage"], "build")
        self.assertEqual(receipt["exit_code"], 7)
        # No container was ever created, which the receipt must not confuse with a cleaned-up one.
        self.assertIsNone(receipt["run_exit_code"])
        self.assertFalse(receipt["container_created"])
        self.assertFalse(receipt["container_removed"])
        self.assertFalse(receipt["cleanup_failed"])
        self.assertEqual(receipt["container_id"], "")
        self.assertEqual(self.containers(), [])
        self.assertFalse((self.results / "cpu-container.cid").exists())

    def test_failed_probe_exit_is_recorded_verbatim(self):
        completed = self.run_script(MOCK_RUN_EXIT="3")
        self.assertEqual(completed.returncode, 3, completed.stderr)
        receipt = self.receipt()
        self.assertEqual(receipt["stage"], "run")
        self.assertEqual(receipt["exit_code"], 3)
        self.assertEqual(receipt["run_exit_code"], 3)
        self.assertTrue(receipt["container_created"])
        self.assertTrue(receipt["container_removed"])
        self.assertEqual(self.containers(), [])

    def test_log_failure_preserves_the_probe_exit_code(self):
        (self.bin / "tee").unlink()
        (self.bin / "tee").write_text('#!/bin/bash\n/usr/bin/tee "$@"\n[[ "${1:-}" != -a ]] || exit 6\n', encoding="utf-8")
        (self.bin / "tee").chmod(0o755)
        completed = self.run_script(MOCK_RUN_EXIT="3")
        self.assertEqual(completed.returncode, 6, completed.stderr)
        receipt = self.receipt()
        self.assertEqual(receipt["exit_code"], 6)
        self.assertEqual(receipt["run_exit_code"], 3)
        self.assertTrue(receipt["container_removed"])
        self.assertFalse(receipt["cleanup_failed"])

    def assert_signal_window(self, point: str, signum: str, expected: int):
        hook = self.root / "signal-window.bash"
        hook.write_text(
            textwrap.dedent("""\
            R17_ROOT=$BASHPID R17_FIRED=0
            trap() {
              builtin trap "$@"
              if [[ $BASHPID == "$R17_ROOT" && $R17_FIRED == 0 ]] && {
                [[ $R17_POINT == teardown && $1 == - && ${2:-} == EXIT ]] ||
                [[ $R17_POINT == armed && $1 == cleanup && ${2:-} == EXIT ]];
              }; then
                R17_FIRED=1
                printf '%s\\n' "$R17_POINT" >"$MOCK_STATE_DIR/signal-fired"
                kill -s "$R17_SIGNAL" "$$"
              fi
            }
            r17_debug() {
              local command=$1
              [[ $BASHPID == "$R17_ROOT" ]] || return 0
              if [[ $R17_FIRED == 0 && $R17_POINT == launch && $command == 'RUN_PID=$!' ]]; then
                R17_FIRED=1
                for attempt in {1..200}; do
                  [[ -s $MOCK_STATE_DIR/worker.pid ]] && break
                  sleep 0.01
                done
                printf '%s\\n' "$R17_POINT" >"$MOCK_STATE_DIR/signal-fired"
                kill -s "$R17_SIGNAL" "$$"
              fi
              return 0
            }
            set -T
            trap 'r17_debug "$BASH_COMMAND"' DEBUG
            """),
            encoding="utf-8",
        )
        env = self.env(BASH_ENV=str(hook), R17_POINT=point, R17_SIGNAL=signum, MOCK_RUN_WORKER="1" if point == "launch" else "0", MOCK_RUN_SLEEP="30" if point == "launch" else "0")
        process = subprocess.Popen(["bash", str(RUN_SH)], cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, expected, stderr)
            self.assertEqual((self.state / "signal-fired").read_text().strip(), point)
            receipt = self.receipt()
            self.assertEqual(receipt["exit_code"], expected)
            self.assertEqual(self.containers(), [])
            if point == "launch":
                self.assertEqual(receipt["run_exit_code"], 143)
                self.assertFalse(receipt["cleanup_failed"])
                worker = int((self.state / "worker.pid").read_text())
                stat = Path(f"/proc/{worker}/stat")
                self.assertTrue(not stat.exists() or stat.read_text().split()[2] == "Z", "probe group survived the receipt")
            elif point == "armed":
                self.assertIsNone(receipt["run_exit_code"])
                self.assertFalse(receipt["container_created"])
        finally:
            for pid in (int((self.state / "probe.pid").read_text()) if (self.state / "probe.pid").exists() else None, process.pid):
                if pid is not None:
                    with suppress(ProcessLookupError):
                        os.killpg(pid, signal.SIGKILL)
            process.communicate(timeout=5)

    def test_signal_after_exit_trap_records_matching_status(self):
        self.assert_signal_window("armed", "TERM", 143)

    def test_sigint_after_exit_trap_records_matching_status(self):
        self.assert_signal_window("armed", "INT", 130)

    def test_signal_before_probe_pid_assignment_reaps_the_group(self):
        self.assert_signal_window("launch", "TERM", 143)

    def test_signal_during_trap_disarming_cannot_abort_cleanup(self):
        self.assert_signal_window("teardown", "TERM", 0)

    def test_interrupted_rm_is_cleaned_up_by_cid(self):
        completed = self.run_script(MOCK_RUN_EXIT="4", MOCK_RUN_LEAVE_CONTAINER="1")
        self.assertEqual(completed.returncode, 4, completed.stderr)
        receipt = self.receipt()
        self.assertEqual(receipt["run_exit_code"], 4)
        self.assertTrue(receipt["container_removed"])
        self.assertFalse(receipt["cleanup_failed"])
        self.assertEqual(self.containers(), [])
        calls = (self.state / "calls.log").read_text()
        # Removal must be addressed by id: this run's name is reused by nothing, but a name
        # filter would still be the wrong lookup for a cleanup that force-removes what it finds.
        self.assertIn(f"rm -f {receipt['container_id']}", calls)
        self.assertNotIn("--filter name=", calls)

    def test_unremovable_container_is_reported_not_hidden(self):
        completed = self.run_script(MOCK_RUN_LEAVE_CONTAINER="1", MOCK_RM_FAILS="1")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        receipt = self.receipt()
        self.assertEqual(receipt["exit_code"], 1)
        self.assertEqual(receipt["run_exit_code"], 0)
        self.assertFalse(receipt["container_removed"])
        self.assertTrue(receipt["cleanup_failed"])
        self.assertEqual(self.containers(), [f"container-{receipt['container_id']}.json"])

    def test_container_listing_failure_is_reported_not_as_removed(self):
        completed = self.run_script(MOCK_RUN_LEAVE_CONTAINER="1", MOCK_LS_FAILS="1")
        receipt = self.receipt()
        self.assertTrue(receipt["container_created"])
        self.assertFalse(receipt["container_removed"], receipt)
        self.assertTrue(receipt["cleanup_failed"])
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(receipt["run_exit_code"], 0)
        self.assertEqual(self.containers(), [f"container-{receipt['container_id']}.json"])

    def test_missing_base_image_records_its_own_stage(self):
        completed = self.run_script(MOCK_BASE_IMAGE_MISSING="1")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("docker pull", completed.stderr)
        receipt = self.receipt()
        self.assertEqual(receipt["stage"], "base_image_check")
        self.assertEqual(receipt["exit_code"], 1)
        self.assertFalse(receipt["container_created"])
        self.assertFalse((self.results / "cpu-runner-full.log").exists())

    def test_failed_image_metadata_records_its_own_stage(self):
        completed = self.run_script(MOCK_METADATA_EXIT="5")
        self.assertEqual(completed.returncode, 5)
        receipt = self.receipt()
        self.assertEqual(receipt["stage"], "image_metadata")
        self.assertEqual(receipt["exit_code"], 5)
        self.assertIsNone(receipt["run_exit_code"])
        self.assertFalse(receipt["container_created"])
        # The failure reason belongs in the log, not only in this process's stderr.
        self.assertIn("image inspect --format failed", (self.results / "cpu-runner-full.log").read_text())

    def test_sigterm_during_the_probe_still_writes_a_receipt(self):
        # Started directly rather than through `run_script`: the signal has to arrive while the
        # probe is still running, which means not waiting for the script to finish first.
        env = self.env(MOCK_RUN_SLEEP="3", MOCK_RUN_LEAVE_CONTAINER="1")
        process = subprocess.Popen(["bash", str(RUN_SH)], cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        cid_path = self.results / "cpu-container.cid"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (cid_path.exists() and cid_path.read_text().strip()):
            self.assertIsNone(process.poll(), "run.sh exited before the probe started")
            time.sleep(0.05)
        self.assertTrue(cid_path.read_text().strip(), "probe never wrote its cid")
        process.send_signal(signal.SIGTERM)
        try:
            # Finishing the shell alone is insufficient: an orphaned docker client or tee keeps
            # these pipes open and may still write after the receipt claims teardown is complete.
            process.communicate(timeout=1)
        finally:
            process.communicate(timeout=10)
        # 143 is SIGTERM's conventional shell status, which the TERM trap sets deliberately;
        # the receipt exists because the trap was armed before anything could fail.
        self.assertEqual(process.returncode, 143)
        receipt = self.receipt()
        self.assertEqual(receipt["stage"], "run")
        self.assertEqual(receipt["exit_code"], 143)
        self.assertTrue(receipt["container_created"])
        self.assertTrue(receipt["container_removed"])
        self.assertEqual(self.containers(), [])

    def test_existing_artifacts_are_never_overwritten(self):
        self.results.mkdir(parents=True)
        for artifact in ARTIFACTS:
            with self.subTest(artifact=artifact):
                path = self.results / artifact
                path.write_bytes(b"existing evidence")
                completed = self.run_script()
                self.assertEqual(completed.returncode, 1)
                self.assertIn("Refusing to overwrite existing evidence", completed.stderr)
                self.assertEqual(path.read_bytes(), b"existing evidence")
                # The guard runs before the trap is armed, so refusing must not write a receipt
                # over the evidence it is protecting.
                self.assertEqual(sorted(p.name for p in self.results.iterdir()), [artifact])
                self.assertEqual((self.state / "calls.log").read_text() if (self.state / "calls.log").exists() else "", "")
                path.unlink()

    def assert_surviving_worker_is_cleaned_up(self, *, terminate: bool, leader_sleep: str = "0"):
        process = subprocess.Popen(
            ["bash", str(RUN_SH)],
            cwd=self.root,
            env=self.env(MOCK_RUN_WORKER="1", MOCK_RUN_SLEEP=leader_sleep),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        leader = None
        try:
            deadline = time.monotonic() + 10
            worker_path = self.state / "worker.pid"
            while not worker_path.exists() or not worker_path.read_text().strip():
                self.assertLess(time.monotonic(), deadline, "mock worker never started")
                time.sleep(0.02)
            leader = int((self.state / "probe.pid").read_text())
            worker = int(worker_path.read_text())
            if leader_sleep == "0":
                while Path(f"/proc/{leader}").exists():
                    self.assertLess(time.monotonic(), deadline, "probe leader never exited")
                    time.sleep(0.02)
            if terminate:
                process.send_signal(signal.SIGTERM)
            _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 143 if terminate else 0, stderr)
            worker_stat = Path(f"/proc/{worker}/stat")
            self.assertTrue(not worker_stat.exists() or worker_stat.read_text().split()[2] == "Z", "worker survived cleanup")
            self.assertTrue(self.receipt()["container_removed"])
            self.assertEqual(self.containers(), [])
        finally:
            # The pre-fix runner hangs; kill only groups created by this test before draining pipes.
            for pid in (leader, process.pid):
                if pid is not None:
                    with suppress(ProcessLookupError):
                        os.killpg(pid, signal.SIGKILL)
            process.communicate(timeout=5)

    def test_exited_probe_with_surviving_worker_does_not_block_tee(self):
        self.assert_surviving_worker_is_cleaned_up(terminate=False)

    def test_sigterm_after_probe_exits_kills_surviving_group(self):
        self.assert_surviving_worker_is_cleaned_up(terminate=True)

    def test_sigterm_escalates_for_term_resistant_probe_group(self):
        self.assert_surviving_worker_is_cleaned_up(terminate=True, leader_sleep="30")

    def test_dangling_artifact_symlink_is_also_refused(self):
        self.results.mkdir(parents=True)
        path = self.results / "cpu-smoke-full.log"
        path.symlink_to(self.results / "missing-target")
        completed = self.run_script()
        self.assertEqual(completed.returncode, 1)
        self.assertIn("Refusing to overwrite existing evidence", completed.stderr)
        self.assertTrue(path.is_symlink())
        self.assertFalse((self.results / "cpu-container-receipt.json").exists())

    def test_runs_from_any_directory(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        completed = self.run_script(cwd=elsewhere)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.receipt()["exit_code"], 0)
        # The build context is the repository root regardless of the caller's directory, because
        # the Dockerfile copies `packages/vllm-hmr` as well as this example.
        build = next(line for line in (self.state / "calls.log").read_text().splitlines() if line.startswith("build "))
        self.assertIn(f"--file {EXAMPLE_ROOT / 'Dockerfile'}", build)
        self.assertTrue(build.endswith(f" {EXAMPLE_ROOT.parents[1]}"), build)

    def test_results_directory_is_not_a_relative_docker_volume(self):
        relative = "relative-results"
        completed = self.run_script(VLLM_HMR_RESULTS=relative)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        # Docker reads a relative -v source as a named volume, so the receipt would land in that
        # volume instead of the documented directory.
        self.results = self.root / relative
        self.assertEqual(self.receipt()["exit_code"], 0)
        run = next(line for line in (self.state / "calls.log").read_text().splitlines() if line.startswith("run --rm --name"))
        self.assertIn(f"-v {self.root / relative}:/results", run)


def which_on_default_path(tool: str) -> str | None:
    for directory in ("/usr/bin", "/bin", "/usr/local/bin"):
        candidate = Path(directory) / tool
        if candidate.exists():
            return str(candidate)
    return None


if __name__ == "__main__":
    unittest.main()
