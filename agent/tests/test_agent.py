import argparse
import ast
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from gpu_radar_agent import cli, collector
from gpu_radar_agent.api import APIError, Client, NoRedirect
from gpu_radar_agent.config import load_config, normalize_server, write_config


class ConfigTests(unittest.TestCase):
    def test_https_and_explicit_http_policy(self):
        self.assertEqual(normalize_server("https://radar.example/api/v1/"), "https://radar.example")
        self.assertEqual(normalize_server("http://127.0.0.1:8000"), "http://127.0.0.1:8000")
        self.assertEqual(normalize_server("http://[::1]:8000"), "http://[::1]:8000")
        with self.assertRaises(ValueError):
            normalize_server("http://192.168.1.10:8000")
        self.assertEqual(normalize_server("http://192.168.1.10:8000", True), "http://192.168.1.10:8000")
        for value in ("https://u:p@example.com", "https://example.com?a=b", "file:///tmp/file", "https://example.com:bad"):
            with self.assertRaises(ValueError):
                normalize_server(value)

    def test_private_atomic_config_and_insecure_permission_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "agent.json"
            config = {"server": "https://radar.example", "server_id": "id", "device_token": "device-secret", "name": "lab-a"}
            write_config(path, config)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(load_config(path), config)
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "chmod 600"):
                load_config(path)

    def test_reject_symlink_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("original")
            link = Path(directory) / "agent.json"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                write_config(link, {})
            self.assertEqual(target.read_text(), "original")


class CollectionTests(unittest.TestCase):
    def test_csv_quoted_model_unknown_metrics_and_process_metadata(self):
        gpus = collector.parse_gpu_csv('GPU-one, 0, "NVIDIA A100, test", 81920, 512, 0, N/A, [N/A], Disabled\nGPU-two,1,RTX 4090,24576,N/A,N/A,42,60,[N/A]\n')
        self.assertEqual(gpus[0]["name"], "NVIDIA A100, test")
        self.assertIsNone(gpus[0]["temperature_celsius"])
        self.assertIsNone(gpus[1]["memory_used_mib"])
        self.assertIsNone(gpus[1]["utilization_gpu_percent"])
        with patch.object(collector, "process_metadata", return_value=(None, None)):
            collector.apply_process_csv(gpus, "GPU-one,123,[N/A]\n")
        self.assertTrue(gpus[0]["processes_known"])
        self.assertEqual(gpus[0]["processes"][0], {"pid": 123, "username": None, "name": None, "memory_used_mib": None})

    def test_mig_and_old_driver_never_claim_known_empty_processes(self):
        self.assertFalse(collector.parse_gpu_csv("GPU-one,0,A100,81920,0,0,30,45,Enabled")[0]["processes_known"])
        self.assertFalse(collector.parse_gpu_csv("GPU-one,0,A100,81920,0,0,30,45", includes_mig=False)[0]["processes_known"])

    def test_csv_bad_rows_are_rejected(self):
        for value in ("", "GPU-one,0,A100,81920", "N/A,0,A100,81920,0,0,30,45,Disabled", "GPU-one,1.5,A100,81920,0,0,30,45,Disabled"):
            with self.assertRaises(collector.CollectionError):
                collector.parse_gpu_csv(value)
        gpus = collector.parse_gpu_csv("GPU-one,0,A100,81920,0,0,30,45,Disabled")
        with self.assertRaises(collector.CollectionError):
            collector.apply_process_csv(gpus, "MIG-uuid,123,10")

    def test_numeric_sentinels_are_unknown(self):
        for value in (None, "N/A", "[Not Supported]", "NaN", "inf", -1, 2**64 - 1, True):
            self.assertIsNone(collector.number(value))
        self.assertEqual(collector.number(" 0 "), 0)

    def test_process_query_failure_preserves_metrics_but_marks_unknown(self):
        with patch.object(collector, "smi_query", side_effect=["GPU-one,0,A100,81920,0,0,30,45,Disabled", collector.CollectionError("denied")]):
            gpus = collector.collect_smi()
        self.assertFalse(gpus[0]["processes_known"])
        self.assertEqual(gpus[0]["memory_used_mib"], 0)

    def test_subprocess_timeouts_report_error_without_simulation(self):
        import subprocess
        with patch.object(collector.subprocess, "run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 1)):
            report = collector.collect_bounded(1)
            self.assertEqual(report["collection_status"], "error")
            self.assertEqual(report["gpus"], [])
            with self.assertRaises(collector.CollectionError):
                collector.smi_query("--query-gpu=uuid")

    def test_nvml_unavailable_falls_back_but_all_failures_stay_errors(self):
        with patch.object(collector, "collect_nvml", side_effect=collector.CollectionError("no nvml")), patch.object(collector, "collect_smi", side_effect=collector.CollectionError("no GPU")):
            report = collector.collect()
        self.assertEqual(report["collection_status"], "error")
        self.assertEqual(report["gpus"], [])
        self.assertNotIn("SIMULATION", report["hostname"])

    def test_nvml_mig_enabled_never_reports_known_processes(self):
        class NVMLError(Exception):
            pass

        class NotSupported(NVMLError):
            pass

        nvml = SimpleNamespace(
            NVMLError=NVMLError, NVMLError_NotSupported=NotSupported, NVML_TEMPERATURE_GPU=0,
            nvmlInit=lambda: None, nvmlShutdown=lambda: None, nvmlDeviceGetCount=lambda: 1,
            nvmlDeviceGetHandleByIndex=lambda i: i, nvmlDeviceGetUUID=lambda h: b"GPU-one",
            nvmlDeviceGetName=lambda h: b"A100", nvmlDeviceGetMemoryInfo=lambda h: SimpleNamespace(total=81920 * collector.MIB, used=0),
            nvmlDeviceGetUtilizationRates=lambda h: SimpleNamespace(gpu=0), nvmlDeviceGetTemperature=lambda h, t: 35,
            nvmlDeviceGetPowerUsage=lambda h: 40000, nvmlDeviceGetMigMode=lambda h: (1, 1),
            nvmlDeviceGetComputeRunningProcesses=lambda h: [],
        )
        with patch.object(collector.importlib, "import_module", return_value=nvml):
            gpu = collector.collect_nvml()[0]
        self.assertFalse(gpu["processes_known"])
        self.assertEqual(gpu["memory_total_mib"], 81920)


class APITests(unittest.TestCase):
    def test_redirects_disabled(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))

    def test_http_error_body_cannot_leak_secret(self):
        client = Client("https://radar.example")
        client.opener = Mock()
        client.opener.open.side_effect = HTTPError("https://radar.example", 401, "failed", {}, io.BytesIO(b"secret-password"))
        with self.assertRaises(APIError) as caught:
            client.request("POST", "/agent/report", token="secret-token")
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn("secret", str(caught.exception))

    def test_login_enroll_logout_chain_saves_only_device_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.json"
            args = argparse.Namespace(config=str(path), server="https://radar.example", allow_insecure_http=False, name="lab-a", username="alice")
            client = Mock()
            client.request.side_effect = [{"access_token": "account-secret"}, {"server_id": "server-id", "device_token": "device-secret", "name": "lab-a"}, None]
            with patch.object(cli, "Client", return_value=client), patch.object(cli.getpass, "getpass", return_value="password-secret"), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.login(args), 0)
            saved = path.read_text()
            self.assertIn("device-secret", saved)
            self.assertNotIn("account-secret", saved)
            self.assertNotIn("password-secret", saved)
            self.assertNotIn("alice", saved)
            self.assertEqual([call.args[1] for call in client.request.call_args_list], ["/auth/login", "/agents/enroll", "/auth/logout"])
            self.assertEqual(client.request.call_args_list[1].kwargs["token"], "account-secret")

    def test_failed_enrollment_still_revokes_account_session(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(config=directory + "/agent.json", server="https://radar.example", allow_insecure_http=False, name="lab-a", username="alice")
            client = Mock()
            client.request.side_effect = [{"access_token": "account-secret"}, APIError(500, "server failed"), None]
            with patch.object(cli, "Client", return_value=client), patch.object(cli.getpass, "getpass", return_value="secret"):
                with self.assertRaises(APIError):
                    cli.login(args)
            self.assertEqual(client.request.call_args_list[-1].args[1], "/auth/logout")
            self.assertFalse(Path(args.config).exists())

    def test_revoked_device_stops_instead_of_retries(self):
        client = Mock()
        client.request.side_effect = [{"status": "ok", "server_time": "2026-10-13T00:00:00Z"}, APIError(401, "revoked")]
        args = argparse.Namespace(request_timeout=1, simulate=True, interval=5, once=False)
        config = {"server": "https://radar.example", "name": "lab-a", "device_token": "secret"}
        with patch.object(cli, "Client", return_value=client):
            self.assertEqual(cli.run_loop(args, config, threading.Event()), 3)
        self.assertEqual(client.request.call_count, 2)

    def test_config_argument_before_and_after_command(self):
        self.assertEqual(cli.parser().parse_args(["--config", "/tmp/a", "status"]).config, "/tmp/a")
        self.assertEqual(cli.parser().parse_args(["status", "--config", "/tmp/b"]).config, "/tmp/b")

    def test_nonfinite_interval_rejected_before_loading_credentials(self):
        args = cli.parser().parse_args(["run", "--interval", "nan"])
        with self.assertRaises(ValueError):
            cli.run(args)

    def test_collect_needs_no_binding_and_emits_only_json(self):
        report = collector.error_report("driver unavailable")
        output = io.StringIO()
        with patch.object(cli, "collect_bounded", return_value=report), redirect_stdout(output):
            result = cli.main(["collect", "--collection-timeout", "3"])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output.getvalue()), report)


class CompatibilityTests(unittest.TestCase):
    def test_python36_syntax(self):
        root = Path(__file__).resolve().parents[1]
        if sys.version_info < (3, 8):
            self.skipTest("feature_version grammar validation needs a modern build interpreter")
        for path in list((root / "src" / "gpu_radar_agent").glob("*.py")) + [root / "build-zipapp.py"]:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 6))

    def test_zipapp_collect_finds_worker_without_installed_package(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "agent.pyz"
            subprocess.run([sys.executable, str(root / "build-zipapp.py"), "--output", str(archive)], check=True, stdout=subprocess.PIPE, universal_newlines=True)
            # -S removes site-packages; this must import from the zipapp itself.
            result = subprocess.run([sys.executable, "-S", str(archive), "collect"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=15)
            report = json.loads(result.stdout)
            self.assertIn(report["collection_status"], {"ok", "error"})
            self.assertEqual(result.returncode, 0 if report["collection_status"] == "ok" else 2)
            self.assertNotEqual(report.get("error"), "GPU collection worker failed.")
            self.assertNotIn("SIMULATION", report["hostname"])


if __name__ == "__main__":
    unittest.main()
