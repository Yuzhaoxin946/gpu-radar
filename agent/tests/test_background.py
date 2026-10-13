import fcntl
import json
import logging
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from gpu_radar_agent import background, cli
from gpu_radar_agent.config import write_config


class BackgroundInterfaceTests(unittest.TestCase):
    def test_config_position_and_actions(self):
        for arguments in (
            ["--config", "/tmp/device.json", "background", "start"],
            ["background", "--config", "/tmp/device.json", "start"],
            ["background", "start", "--config", "/tmp/device.json"],
        ):
            args = cli.parser().parse_args(arguments)
            self.assertEqual(args.config, "/tmp/device.json")
            self.assertEqual(args.background_action, "start")
        for action in ("start", "stop", "status"):
            self.assertEqual(cli.parser().parse_args(["background", action]).background_action, action)

    def test_log_files_are_private_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.json.background.log"
            handler = background.PrivateRotatingFileHandler(str(path), maxBytes=500, backupCount=1)
            try:
                for number in range(100):
                    handler.emit(logging.LogRecord("test", logging.INFO, "", 1, "%s %s", (number, "x" * 100), None))
            finally:
                handler.close()
            files = sorted(Path(directory).iterdir())
            self.assertEqual(len(files), 2)
            for item in files:
                self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600)
                self.assertLess(item.stat().st_size, 650)

    def test_management_lock_wait_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "agent.json"
            with background.management_lock(config):
                started = time.monotonic()
                with self.assertRaisesRegex(ValueError, "still running"):
                    with background.management_lock(config, deadline=started + 0.1):
                        self.fail("A second manager acquired the same exclusive lock")
                self.assertLess(time.monotonic() - started, 1)

    def test_state_and_log_symlinks_cannot_redirect_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("do not replace")
            target.chmod(0o600)
            link = Path(directory) / "state"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                background._read_private(link)
            with self.assertRaises(OSError):
                background.PrivateRotatingFileHandler(str(link), maxBytes=500, backupCount=1)
            self.assertEqual(target.read_text(), "do not replace")


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/proc/self/stat").exists(),
                     "Linux /proc process identity and real detached workers")
class BackgroundProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive_directory = tempfile.TemporaryDirectory(prefix="gpu-radar-test-archive-")
        cls.archive = Path(cls.archive_directory.name) / "gpu-radar-agent.pyz"
        builder = Path(__file__).resolve().parents[1] / "build-zipapp.py"
        subprocess.run([sys.executable, str(builder), "--output", str(cls.archive)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    @classmethod
    def tearDownClass(cls):
        cls.archive_directory.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="gpu-radar-background-test-")
        self.root = Path(self.directory.name)
        self.agent = self.root / "gpu-radar-agent.pyz"
        shutil.copyfile(str(self.archive), str(self.agent))
        self.config = self.root / "device.json"
        write_config(self.config, {"server": "http://127.0.0.1:1", "server_id": "test-id",
                                   "device_token": "private-test-token", "name": "test-worker"})
        self.state = background.paths(self.config)
        self.original_metadata = None
        self.children = []

    def tearDown(self):
        # Restore the authentic metadata after tests intentionally corrupt it.
        if self.original_metadata is not None:
            write_config(self.state["metadata"], self.original_metadata)
        self.invoke("stop")
        for process in self.children:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=30)
        self.directory.cleanup()

    def command(self, action):
        return [sys.executable, str(self.agent), "background", action, "--config", str(self.config)]

    def invoke(self, action, artifact=None):
        command = self.command(action)
        if artifact is not None:
            command[1] = str(artifact)
        return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=35,
                              universal_newlines=True)

    def started(self):
        result = self.invoke("start")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        metadata = json.loads(self.state["metadata"].read_text())
        self.original_metadata = metadata
        return metadata

    def test_start_is_idempotent_and_stop_removes_own_worker(self):
        metadata = self.started()
        self.assertEqual(self.invoke("status").returncode, 0)
        again = self.invoke("start")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(self.state["metadata"].read_text())["pid"], metadata["pid"])
        self.assertEqual(os.getsid(metadata["pid"]), metadata["pid"])
        self.assertEqual(stat.S_IMODE(self.state["metadata"].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.state["log"].stat().st_mode), 0o600)
        for file in (self.state["metadata"], self.state["log"]):
            self.assertNotIn("private-test-token", file.read_text())
        stopped = self.invoke("stop")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(self.invoke("status").returncode, 3)
        self.assertFalse(self.state["metadata"].exists())
        self.original_metadata = None

    def test_parallel_starts_create_one_worker(self):
        callers = [subprocess.Popen(self.command("start"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                   for _ in range(3)]
        for caller in callers:
            output, error = caller.communicate(timeout=15)
            self.assertEqual(caller.returncode, 0, (output, error))
        self.original_metadata = json.loads(self.state["metadata"].read_text())
        self.assertEqual(self.invoke("status").returncode, 0)

    def test_worker_survives_terminal_and_login_session_exit(self):
        launcher, terminal = pty.fork()
        if launcher == 0:
            os.execv(sys.executable, self.command("start"))
        deadline = time.monotonic() + 15
        output = b""
        exited = False
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([terminal], [], [], 0.1)
                if ready:
                    try:
                        output += os.read(terminal, 8192)
                    except OSError:
                        pass
                done, status = os.waitpid(launcher, os.WNOHANG)
                if done:
                    self.assertEqual(status, 0, output)
                    exited = True
                    break
            self.assertTrue(exited, output)
        finally:
            os.close(terminal)
            if not exited:
                os.kill(launcher, signal.SIGTERM)
                os.waitpid(launcher, 0)
        metadata = json.loads(self.state["metadata"].read_text())
        self.original_metadata = metadata
        self.assertNotEqual(os.getsid(metadata["pid"]), launcher)
        # Also verify the worker explicitly ignores a SIGHUP from its launcher.
        os.kill(metadata["pid"], signal.SIGHUP)
        time.sleep(0.2)
        self.assertEqual(self.invoke("status").returncode, 0)
        self.assertEqual(os.readlink("/proc/{}/fd/0".format(metadata["pid"])), "/dev/null")

    def test_stale_boot_and_pid_start_time_never_stop_live_process(self):
        metadata = self.started()
        for key, value in (("boot_id", "old-boot"), ("start_ticks", metadata["start_ticks"] + 1)):
            stale = dict(metadata)
            stale[key] = value
            write_config(self.state["metadata"], stale)
            self.assertEqual(self.invoke("status").returncode, 3)
            self.assertEqual(self.invoke("stop").returncode, 0)
            self.assertIsNotNone(background._process_identity(metadata["pid"]))
            duplicate = self.invoke("start")
            self.assertEqual(duplicate.returncode, 1)
            self.assertIn("already running", duplicate.stderr)
        write_config(self.state["metadata"], metadata)
        self.assertEqual(self.invoke("status").returncode, 0)

    def test_arbitrary_live_process_with_matching_pid_is_not_an_agent(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(40)"])
        self.children.append(process)
        identity = background._process_identity(process.pid)
        identity.update(version=1, config=str(self.config), nonce="a" * 32)
        write_config(self.state["metadata"], identity)
        self.assertEqual(self.invoke("status").returncode, 1)
        self.assertEqual(self.invoke("stop").returncode, 1)
        self.assertIsNone(process.poll())
        self.assertFalse(self.state["request"].exists())

    def test_downloaded_artifact_can_manage_worker_after_atomic_update(self):
        metadata = self.started()
        downloaded = self.root / "downloaded-agent.pyz"
        shutil.copyfile(str(self.archive), str(downloaded))
        replacement = self.root / "replacement.pyz"
        shutil.copyfile(str(self.archive), str(replacement))
        os.replace(str(replacement), str(self.agent))
        self.assertEqual(self.invoke("status", artifact=downloaded).returncode, 0)
        stopped = self.invoke("stop", artifact=downloaded)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assertIsNone(background._process_identity(metadata["pid"]))
        self.original_metadata = None

    def test_foreground_reporting_lock_prevents_background_duplicate(self):
        process = subprocess.Popen([sys.executable, str(self.agent), "run", "--config", str(self.config)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(process)
        deadline = time.monotonic() + 5
        locked = False
        while time.monotonic() < deadline:
            try:
                with cli.run_lock(self.config):
                    pass
            except ValueError:
                locked = True
                break
            time.sleep(0.05)
        self.assertTrue(locked)
        self.assertEqual(self.invoke("start").returncode, 1)
        self.assertIsNone(process.poll())
        self.assertFalse(self.state["metadata"].exists())

    def test_stop_tolerates_exit_snapshot_with_empty_cmdline(self):
        metadata = self.started()
        actual_identity = background._process_identity
        calls = []

        def exit_snapshot(pid):
            calls.append(pid)
            if len(calls) == 2:
                # Linux can expose an empty cmdline after the initial stat
                # read while the verified process is in the act of exiting.
                partial = dict(metadata)
                partial["command"] = [""]
                return partial
            return actual_identity(pid)

        with patch.object(background, "_process_identity", side_effect=exit_snapshot):
            self.assertEqual(background.stop(self.config), 0)
        self.assertGreaterEqual(len(calls), 3)
        self.assertIsNone(actual_identity(metadata["pid"]))
        self.original_metadata = None

    def test_start_timeout_reaps_its_unconfirmed_child(self):
        actual_popen = subprocess.Popen
        spawned = []

        def record_child(*args, **kwargs):
            process = actual_popen(*args, **kwargs)
            spawned.append(process)
            return process

        slow_command = [sys.executable, "-c", "import time; time.sleep(60)"]
        with patch.object(background, "START_TIMEOUT", 0.2), \
                patch.object(background, "_command", return_value=slow_command), \
                patch.object(background.subprocess, "Popen", side_effect=record_child):
            with self.assertRaisesRegex(ValueError, "could not be confirmed"):
                background.start(self.config)
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll())
        self.assertIsNone(background._process_identity(spawned[0].pid))
        self.assertFalse(self.state["metadata"].exists())

    def test_stop_nonce_must_match_the_running_worker(self):
        metadata = self.started()
        request = {key: metadata[key] for key in ("nonce", "pid", "start_ticks", "boot_id")}
        request["nonce"] = "0" * 32
        write_config(self.state["request"], request)
        time.sleep(0.25)
        self.assertEqual(self.invoke("status").returncode, 0)
        self.assertEqual(self.invoke("stop").returncode, 0)
        self.original_metadata = None


if __name__ == "__main__":
    unittest.main()
