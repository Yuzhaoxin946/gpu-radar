"""Installer acceptance tests: real files/hash checks, mocked network and service manager."""

import ast
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import pty
import stat
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from gpu_radar_agent.config import load_config, write_config


SCRIPT = Path(__file__).resolve().parents[1] / "install.sh"


def load_installer():
    script = SCRIPT.read_text(encoding="utf-8")
    marker = "<<'PYTHON_INSTALLER'\n"
    source = script.split(marker, 1)[1].split("\nPYTHON_INSTALLER\n", 1)[0]
    module = ModuleType("gpu_radar_installer_test")
    exec(compile(source, str(SCRIPT), "exec"), module.__dict__)
    return module, source


class Response(io.BytesIO):
    status = 200


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.installer, self.source = load_installer()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="radar-installer-test-"))
        # macOS /var is a symlink. The installer correctly disallows symlink homes.
        self.home = Path(directory).resolve() / "home"
        self.home.mkdir(mode=0o700)
        self.active_by_host = {}
        self.enabled_services = set()
        self.select_host("1" * 32)
        self.artifact = b"verified GPU Radar test artifact\n"
        self.installer.ARTIFACT_SHA256 = hashlib.sha256(self.artifact).hexdigest()
        self.download_body = self.artifact
        self.active = False
        self.enabled = False
        self.linger = True
        self.allow_linger = True
        self.bus_available = True
        self.restart_failure = False
        self.report_code = 0
        self.collect_code = 0
        self.login_count = 0
        self.commands = []
        self.requests = []
        self.handlers = []
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.stack.enter_context(patch.dict(os.environ, {"HOME": str(self.home)}))
        # These OS facts are tested separately. Everything below uses real private files.
        self.stack.enter_context(patch.object(self.installer, "check_platform"))
        self.stack.enter_context(patch.object(self.installer, "read_machine_id", side_effect=lambda: self.machine_id))
        self.stack.enter_context(patch.object(self.installer.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="linux-user", pw_dir=str(self.home))))
        self.stack.enter_context(patch.object(self.installer.shutil, "which", side_effect=lambda name: None if name == "sudo" else "/usr/bin/" + name))
        self.stack.enter_context(patch.object(self.installer, "build_opener", side_effect=self.build_opener))
        self.stack.enter_context(patch.object(self.installer.subprocess, "run", side_effect=self.run_command))
        self.stack.enter_context(patch.object(self.installer.time, "sleep"))
        self.stack.enter_context(patch.object(self.installer, "open_tty", side_effect=lambda: io.StringIO()))
        self.stack.enter_context(redirect_stdout(self.stdout))
        self.stack.enter_context(redirect_stderr(self.stderr))

    def select_host(self, machine_id):
        self.machine_id = machine_id
        self.service = "gpu-radar-agent-" + machine_id + ".service"
        self.package = self.home / ".local/share/gpu-radar/hosts" / machine_id / "gpu-radar-agent.pyz"
        self.config = self.home / ".config/gpu-radar/hosts" / machine_id / "agent.json"
        self.unit = self.home / ".config/systemd/user" / self.service

    @property
    def active(self):
        return self.active_by_host.get(self.machine_id, False)

    @active.setter
    def active(self, value):
        self.active_by_host[self.machine_id] = value

    @property
    def enabled(self):
        return self.service in self.enabled_services

    @enabled.setter
    def enabled(self, value):
        if value:
            self.enabled_services.add(self.service)
        else:
            self.enabled_services.discard(self.service)

    def build_opener(self, handler):
        self.handlers.append(handler)
        return SimpleNamespace(open=self.urlopen)

    def urlopen(self, request, timeout):
        self.requests.append(request)
        if request.full_url in (self.installer.ARTIFACT_URL, self.installer.ALIYUN_ARTIFACT_URL):
            return Response(self.download_body)
        self.assertEqual(request.full_url, "https://123.56.135.166/api/v1/health")
        return Response(b'{"status":"ok"}')

    def run_command(self, args, **kwargs):
        args = [str(arg) for arg in args]
        self.commands.append(args)
        code, output = 0, ""
        if args[0] == sys.executable:
            action = args[2]
            if action == "collect":
                code = self.collect_code
                output = json.dumps({"collection_status": "ok", "gpus": [{"uuid": "GPU-test"}]})
            elif action == "status":
                try:
                    load_config(Path(args[args.index("--config") + 1]))
                except (ValueError, OSError):
                    code = 1
            elif action == "login":
                self.login_count += 1
                config = Path(args[args.index("--config") + 1])
                self.assertFalse(config.exists(), "Must not replace an existing device binding")
                self.assertEqual(config, self.config, "Enrollment must target this host's own configuration")
                self.assertEqual(args[args.index("--server") + 1], "https://123.56.135.166")
                write_config(config, {
                    "version": 1, "server": "https://123.56.135.166", "server_id": "new-id-" + str(self.login_count),
                    "device_token": "TEST-DEVICE-SECRET-" + str(self.login_count), "name": args[args.index("--name") + 1],
                    "allow_insecure_http": False,
                })
            elif action == "run":
                self.assertIn("--once", args)
                self.assertEqual(Path(args[args.index("--config") + 1]), self.config)
                code = self.report_code
            else:
                self.fail("Unexpected Python action: " + str(args))
        elif args[0] == "systemctl":
            action = args[2]
            if action not in {"show-environment", "daemon-reload"}:
                self.assertEqual(args[-1], self.service, "Never operate another host's unit in a shared HOME")
            if action == "show-environment":
                code = 0 if self.bus_available else 1
            elif action == "is-active":
                code = 0 if self.active else 3
            elif action == "is-enabled":
                code = 0 if self.enabled else 1
            elif action == "stop":
                self.active = False
            elif action == "enable":
                self.enabled = True
            elif action == "disable":
                # Disabling a missing unit can fail and leave an enabled link behind.
                if self.unit.exists():
                    self.enabled = False
                else:
                    code = 1
            elif action == "restart":
                if self.restart_failure:
                    self.restart_failure = False
                    code = 1
                else:
                    self.active = True
            elif action == "start":
                self.active = True
            elif action != "daemon-reload":
                self.fail("Unexpected systemctl command: " + str(args))
        elif args[0] == "loginctl":
            if "show-user" in args:
                output = "yes\n" if self.linger else "no\n"
            elif "enable-linger" in args:
                if self.allow_linger:
                    self.linger = True
                else:
                    code = 1
            else:
                self.fail("Unexpected loginctl command: " + str(args))
        else:
            self.fail("Unexpected external command: " + str(args))
        return SimpleNamespace(returncode=code, stdout=output, stderr="TEST-CHILD-SECRET")

    def old_installation(self, account="alice", server="https://123.56.135.166"):
        for path in (self.package, self.unit):
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            path.write_bytes(b"existing-" + path.name.encode())
            path.chmod(0o600)
        write_config(self.config, {
            "server": server, "server_id": "existing-id", "device_token": "TEST-DEVICE-SECRET",
            "name": "lab-a", "account_username": account, "machine_id": self.machine_id,
        })
        self.active = self.enabled = True
        return {path: path.read_bytes() for path in (self.package, self.unit, self.config)}

    def assert_preserved(self, before):
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertTrue(self.active)
        self.assertTrue(self.enabled)
        self.assertEqual(self.login_count, 0)

    def test_new_install_is_private_persistent_and_rerun_reuses_binding(self):
        argv = ["--username", "alice", "--name", "lab-a"]
        self.assertEqual(self.installer.main(argv), 0)
        first = self.config.read_bytes()
        self.assertEqual(self.login_count, 1)
        self.assertTrue(self.active and self.enabled and self.linger)
        self.assertEqual(load_config(self.config)["account_username"], "alice")
        self.assertEqual(load_config(self.config)["machine_id"], self.machine_id)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.config.parent.stat().st_mode), 0o700)
        self.assertEqual(self.package.read_bytes(), self.artifact)
        unit = self.unit.read_text()
        self.assertIn("--interval 5", unit)
        self.assertIn("RestartPreventExitStatus=3 4", unit)
        self.assertIn("WantedBy=default.target", unit)
        self.assertIn("ConditionHost=" + self.machine_id, unit)
        self.assertIn("/hosts/" + self.machine_id + "/agent.json", unit)
        self.assertEqual(self.installer.main(argv), 0)
        self.assertEqual(self.login_count, 1)
        self.assertEqual(self.config.read_bytes(), first)
        self.assertNotIn("TEST-DEVICE-SECRET", self.stdout.getvalue() + self.stderr.getvalue())
        self.assertNotIn("TEST-CHILD-SECRET", self.stdout.getvalue() + self.stderr.getvalue())

    def test_check_is_read_only_for_home_and_services(self):
        marker = self.home / "unrelated"
        marker.write_text("keep")
        self.assertEqual(self.installer.main(["--check"]), 0)
        self.assertEqual(list(self.home.iterdir()), [marker])
        self.assertEqual(marker.read_text(), "keep")
        self.assertEqual(self.login_count, 0)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.commands[0][2], "collect")
        self.assertEqual(self.requests[0].full_url, self.installer.ARTIFACT_URL)
        self.assertIsInstance(self.handlers[0], self.installer.ArtifactRedirect)
        self.assertIsInstance(self.handlers[1], self.installer.NoRedirect)

    def test_aliyun_mirror_is_explicit_and_does_not_redirect(self):
        self.assertEqual(self.installer.main(["--check", "--mirror", "aliyun"]), 0)
        self.assertEqual(self.requests[0].full_url, self.installer.ALIYUN_ARTIFACT_URL)
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(all(isinstance(handler, self.installer.NoRedirect) for handler in self.handlers))
        self.assertEqual(list(self.home.iterdir()), [])

    def test_unknown_mirror_fails_before_download_or_service_changes(self):
        with self.assertRaises(SystemExit) as caught:
            self.installer.main(["--check", "--mirror", "untrusted"])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.commands, [])

    def test_checksum_failure_preserves_running_install_without_commands(self):
        before = self.old_installation()
        self.download_body = b"corrupt or unexpected download"
        with self.assertRaisesRegex(self.installer.InstallError, "checksum mismatch"):
            self.installer.main(["--username", "alice"])
        self.assert_preserved(before)
        self.assertEqual(self.commands, [])

    def test_gpu_preflight_failure_never_stops_current_service(self):
        before = self.old_installation()
        self.collect_code = 2
        with self.assertRaisesRegex(self.installer.InstallError, "collection failed"):
            self.installer.main(["--username", "alice"])
        self.assert_preserved(before)
        self.assertTrue(all(args[0] != "systemctl" for args in self.commands))

    def test_account_mismatch_preserves_binding_and_running_service(self):
        before = self.old_installation(account="another-user")
        with self.assertRaisesRegex(self.installer.InstallError, "different App account"):
            self.installer.main(["--username", "alice"])
        self.assert_preserved(before)
        self.assertTrue(all(args[0] != "systemctl" for args in self.commands))

    def test_foreign_backend_is_never_rebound(self):
        before = self.old_installation(server="https://elsewhere.example")
        with self.assertRaisesRegex(self.installer.InstallError, "different backend"):
            self.installer.main(["--username", "alice"])
        self.assert_preserved(before)

    def test_linger_denial_does_not_enroll_or_claim_reboot_success(self):
        self.linger = self.allow_linger = False
        with self.assertRaisesRegex(self.installer.InstallError, "Reboot startup was NOT enabled"):
            self.installer.main(["--username", "alice", "--name", "lab-a"])
        self.assertEqual(self.login_count, 0)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.package.exists())
        self.assertFalse(self.unit.exists())
        self.assertNotIn("starts after reboot", self.stdout.getvalue())

    def test_missing_user_manager_fails_before_account_enrollment(self):
        self.bus_available = False
        with self.assertRaisesRegex(self.installer.InstallError, "user manager is unavailable"):
            self.installer.main(["--username", "alice", "--name", "lab-a"])
        self.assertEqual(self.login_count, 0)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.unit.exists())

    def test_restart_failure_restores_files_and_running_service(self):
        before = self.old_installation()
        self.restart_failure = True
        with self.assertRaisesRegex(self.installer.InstallError, "Command failed") as caught:
            self.installer.main(["--username", "alice"])
        self.assert_preserved(before)
        self.assertNotIn("TEST-CHILD-SECRET", str(caught.exception))
        self.assertIn(["systemctl", "--user", "start", self.service], self.commands)

    def test_first_report_failure_keeps_new_binding_for_retry(self):
        self.report_code = 1
        args = ["--username", "alice", "--name", "lab-a"]
        with self.assertRaisesRegex(self.installer.InstallError, "First real report failed"):
            self.installer.main(args)
        self.assertEqual(self.login_count, 1)
        binding = self.config.read_bytes()
        self.assertFalse(self.package.exists())
        self.assertFalse(self.unit.exists())
        self.report_code = 0
        self.assertEqual(self.installer.main(args), 0)
        self.assertEqual(self.login_count, 1)
        self.assertEqual(self.config.read_bytes(), binding)

    def test_fresh_install_restart_failure_removes_enabled_service(self):
        self.restart_failure = True
        with self.assertRaisesRegex(self.installer.InstallError, "Command failed"):
            self.installer.main(["--username", "alice", "--name", "lab-a"])
        self.assertFalse(self.active)
        self.assertFalse(self.enabled)
        self.assertFalse(self.package.exists())
        self.assertFalse(self.unit.exists())
        self.assertEqual(self.login_count, 1)
        self.assertTrue(self.config.exists(), "Keep new binding so retry never duplicates it")

    def test_overridden_home_fails_before_files_or_services_change(self):
        account = SimpleNamespace(pw_name="linux-user", pw_dir=str(self.home / "different"))
        with patch.object(self.installer.pwd, "getpwuid", return_value=account):
            with self.assertRaisesRegex(self.installer.InstallError, "HOME differs"):
                self.installer.main(["--username", "alice"])
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(self.commands, [])

    def test_second_installer_is_rejected_without_enrollment(self):
        lock = self.package.parent / ".install.lock"
        lock.parent.mkdir(parents=True, mode=0o700)
        with lock.open("w") as owner:
            self.installer.fcntl.flock(owner.fileno(), self.installer.fcntl.LOCK_EX | self.installer.fcntl.LOCK_NB)
            with self.assertRaisesRegex(self.installer.InstallError, "Another GPU Radar installer"):
                self.installer.main(["--username", "alice"])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.login_count, 0)

    def test_two_hosts_sharing_home_keep_distinct_bindings_and_guarded_units(self):
        first_host = self.machine_id
        first_args = ["--username", "alice", "--name", "lab-a"]
        self.assertEqual(self.installer.main(first_args), 0)
        first_binding = load_config(self.config)
        first_files = {path: path.read_bytes() for path in (self.package, self.config, self.unit)}
        first_service = self.service

        second_host = "2" * 32
        self.select_host(second_host)
        second_args = ["--username", "alice", "--name", "lab-b"]
        self.assertEqual(self.installer.main(second_args), 0)
        second_binding = load_config(self.config)
        self.assertEqual(self.login_count, 2)
        self.assertNotEqual(first_binding["server_id"], second_binding["server_id"])
        self.assertNotEqual(first_binding["device_token"], second_binding["device_token"])
        self.assertEqual(second_binding["machine_id"], second_host)
        self.assertIn("ConditionHost=" + second_host, self.unit.read_text())
        self.assertIn("/hosts/" + second_host + "/gpu-radar-agent.pyz", self.unit.read_text())
        self.assertEqual({path: path.read_bytes() for path in first_files}, first_files)
        self.assertTrue(self.active_by_host[first_host])
        self.assertTrue(self.active_by_host[second_host])
        self.assertIn(first_service, self.enabled_services)
        self.assertIn(self.service, self.enabled_services)

        self.assertEqual(self.installer.main(second_args), 0)
        self.select_host(first_host)
        self.assertEqual(self.installer.main(first_args), 0)
        self.assertEqual(self.login_count, 2, "Each physical host enrolls once, regardless of shared HOME")
        self.assertEqual({path: path.read_bytes() for path in first_files}, first_files)

    def test_second_host_failure_never_rolls_back_first_host_installation(self):
        first_host = self.machine_id
        self.assertEqual(self.installer.main(["--username", "alice", "--name", "lab-a"]), 0)
        first_files = {path: path.read_bytes() for path in (self.package, self.config, self.unit)}
        first_service = self.service
        self.select_host("2" * 32)
        self.restart_failure = True
        with self.assertRaisesRegex(self.installer.InstallError, "Command failed"):
            self.installer.main(["--username", "alice", "--name", "lab-b"])
        self.assertFalse(self.active)
        self.assertFalse(self.enabled)
        self.assertTrue(self.config.exists())
        self.assertFalse(self.package.exists())
        self.assertFalse(self.unit.exists())
        self.assertTrue(self.active_by_host[first_host])
        self.assertIn(first_service, self.enabled_services)
        self.assertEqual({path: path.read_bytes() for path in first_files}, first_files)

    def test_legacy_flat_binding_or_unit_requires_explicit_migration(self):
        for relative in (".config/gpu-radar/agent.json", ".config/systemd/user/gpu-radar-agent.service"):
            with self.subTest(path=relative):
                legacy = self.home / relative
                legacy.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                legacy.write_bytes(b"legacy installation must stay untouched")
                before = {path: path.read_bytes() for path in self.home.rglob("*") if path.is_file()}
                with self.assertRaisesRegex(self.installer.InstallError, "[Ll]egacy"):
                    self.installer.main(["--username", "alice"])
                self.assertEqual({path: path.read_bytes() for path in self.home.rglob("*") if path.is_file()}, before)
                self.assertFalse((self.home / ".local").exists())
                self.assertEqual(self.requests, [])
                self.assertEqual(self.commands, [])
                legacy.unlink()

    def test_preflight_only_can_check_a_legacy_installation_without_migrating(self):
        legacy = self.home / ".config/gpu-radar/agent.json"
        legacy.parent.mkdir(parents=True, mode=0o700)
        legacy.write_bytes(b"legacy binding")
        self.assertEqual(self.installer.main(["--check"]), 0)
        self.assertEqual(legacy.read_bytes(), b"legacy binding")
        self.assertFalse((self.home / ".local").exists())
        self.assertEqual(self.login_count, 0)
        self.assertTrue(all(args[0] != "systemctl" for args in self.commands))

    def test_dangling_legacy_autostart_link_is_not_silently_migrated(self):
        link = self.home / ".config/systemd/user/default.target.wants/gpu-radar-agent.service"
        link.parent.mkdir(parents=True, mode=0o700)
        link.symlink_to("../gpu-radar-agent.service")
        with self.assertRaisesRegex(self.installer.InstallError, "[Ll]egacy"):
            self.installer.main(["--username", "alice"])
        self.assertTrue(link.is_symlink())
        self.assertFalse((self.home / ".local").exists())
        self.assertEqual(self.requests, [])
        self.assertEqual(self.commands, [])

    def test_binding_machine_id_mismatch_is_never_reused(self):
        self.old_installation()
        data = load_config(self.config)
        data["machine_id"] = "2" * 32
        write_config(self.config, data)
        before = {path: path.read_bytes() for path in (self.package, self.config, self.unit)}
        with self.assertRaisesRegex(self.installer.InstallError, "[Mm]achine|[Hh]ost"):
            self.installer.main(["--username", "alice"])
        self.assert_preserved(before)
        self.assertTrue(all(args[0] != "systemctl" for args in self.commands))


class InstallerTerminalTests(unittest.TestCase):
    def test_real_nonseekable_tty_supports_password_handoff(self):
        installer, _ = load_installer()
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        device = os.ttyname(slave)
        file_io = io.FileIO
        # /dev/tty requires a controlling session; redirect only its path to a real PTY.
        with patch.object(installer.io, "FileIO", side_effect=lambda path, mode: file_io(device, mode)):
            with installer.open_tty() as terminal:
                self.assertTrue(terminal.isatty())
                terminal.write("Username: ")
                terminal.flush()
                self.assertIn(b"Username: ", os.read(master, 4096))

    def test_embedded_installer_uses_python36_syntax(self):
        _, source = load_installer()
        if sys.version_info < (3, 8):
            self.skipTest("feature_version grammar validation needs Python 3.8+")
        ast.parse(source, filename=str(SCRIPT), feature_version=(3, 6))


class InstallerMachineIdentityTests(unittest.TestCase):
    def test_machine_id_is_read_from_system_and_invalid_ids_never_fall_back(self):
        installer, _ = load_installer()
        expected = "0123456789abcdef0123456789abcdef"
        with patch.dict(installer.__dict__, {"open": lambda *args, **kwargs: io.StringIO(expected + "\n")}):
            self.assertEqual(installer.read_machine_id(), expected)
        for value in ("", "0" * 32, "1" * 31, "1" * 33, "ABCDEF" * 5 + "AB", "uninitialized", "../../other-host"):
            with self.subTest(value=value):
                with patch.dict(installer.__dict__, {"open": lambda *args, **kwargs: io.StringIO(value)}):
                    with self.assertRaises(installer.InstallError):
                        installer.read_machine_id()
                with self.assertRaises(installer.InstallError):
                    installer.host_paths(Path("/home/researcher"), value)
                with self.assertRaises(installer.InstallError):
                    installer.unit_text(value)

    def test_unreadable_machine_id_stops_install_before_any_home_changes(self):
        installer, _ = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.dict(os.environ, {"HOME": str(home)}), patch.object(installer, "check_platform"), \
                    patch.dict(installer.__dict__, {"open": unittest.mock.Mock(side_effect=FileNotFoundError("no machine id"))}):
                with self.assertRaisesRegex(installer.InstallError, "machine-id"):
                    installer.main(["--username", "alice"])
            self.assertEqual(list(home.iterdir()), [])


class InstallerRedirectTests(unittest.TestCase):
    def setUp(self):
        self.installer, _ = load_installer()

    def test_allowed_asset_redirects_drop_all_origin_credentials(self):
        for host in ("github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"):
            with self.subTest(host=host):
                request = self.installer.Request(self.installer.ARTIFACT_URL, headers={
                    "Authorization": "Bearer TEST-SECRET", "Cookie": "TEST-COOKIE", "Host": "github.com",
                })
                request.add_unredirected_header("Proxy-Authorization", "TEST-PROXY-SECRET")
                target = "https://" + host + "/asset?signature=test-signature"
                redirected = self.installer.ArtifactRedirect().redirect_request(request, None, 302, "", {}, target)
                self.assertEqual(redirected.full_url, target)
                self.assertEqual(redirected.get_method(), "GET")
                self.assertEqual(dict(redirected.header_items()), {"User-agent": self.installer.INSTALLER_USER_AGENT})

    def test_artifact_redirects_reject_unapproved_origins_and_urls(self):
        targets = (
            "http://github.com/asset", "https://elsewhere.example/asset",
            "https://github.com.elsewhere.example/asset", "https://sub.github.com/asset",
            "https://name@github.com/asset", "https://name:secret@github.com/asset",
            "https://github.com:8443/asset", "https://github.com:bad/asset",
            "https://github.com/asset#fragment", "https://github.com/asset\n",
            "https://github.com/asset\x7f", "file:///tmp/asset", "//github.com/asset",
        )
        request = self.installer.Request(self.installer.ARTIFACT_URL)
        for target in targets:
            with self.subTest(target=target):
                with self.assertRaises(self.installer.InstallError):
                    self.installer.ArtifactRedirect().redirect_request(request, None, 302, "", {}, target)

    def test_redirect_chain_is_bounded_and_get_only(self):
        handler = self.installer.ArtifactRedirect()
        request = self.installer.Request(self.installer.ARTIFACT_URL)
        for code in (301, 302, 303, 307, 308):
            request = handler.redirect_request(request, None, code, "", {}, "https://github.com/next")
        with self.assertRaisesRegex(self.installer.InstallError, "five redirects"):
            handler.redirect_request(request, None, 302, "", {}, "https://github.com/sixth")
        with self.assertRaisesRegex(self.installer.InstallError, "unexpected request method"):
            self.installer.ArtifactRedirect().redirect_request(
                self.installer.Request(self.installer.ARTIFACT_URL, data=b"body", method="POST"),
                None, 302, "", {}, "https://github.com/asset")
        with self.assertRaises(self.installer.InstallError):
            self.installer.ArtifactRedirect().redirect_request(request, None, 200, "", {}, "https://github.com/asset")


if __name__ == "__main__":
    unittest.main()
