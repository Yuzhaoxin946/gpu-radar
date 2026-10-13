#!/usr/bin/env python3
"""Isolated HTTP integration check: real agent CLI against a temporary API/database.

Run with the project's virtualenv. Creates no production accounts or bindings.
Classification intervals are shortened only in this temporary test server.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent" / "src"))
from gpu_radar_agent.api import APIError, Client
from gpu_radar_agent.cli import main as agent_main
from gpu_radar_agent.config import load_config


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="gpu-radar-smoke-") as scratch:
        scratch_path = Path(scratch)
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        # Override the window only in an isolated test app factory; the shipped
        # production app always uses its full 300-second window.
        (scratch_path / "smoke_app.py").write_text(
            "from dataclasses import replace\n"
            "from app.config import Settings\n"
            "from app.main import create_app\n"
            "app = create_app(replace(Settings.from_env(), utilization_window_seconds=2))\n"
        )
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "server"), str(scratch_path)]),
               "DATABASE_URL": f"sqlite:///{scratch_path / 'smoke.db'}",
               "REGISTRATION_ENABLED": "true",
               "STALE_AFTER_SECONDS": "3"}
        invitation = secrets.token_urlsafe(24)
        subprocess.run([sys.executable, "-m", "app.manage", "create-invite", "--max-uses", "2", "--code-stdin"],
                       input=invitation, text=True, capture_output=True, check=True, cwd=ROOT / "server", env=env)
        with (scratch_path / "server.log").open("w+") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "smoke_app:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT / "server", env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        with urlopen(base + "/api/v1/health", timeout=1) as response:
                            check(response.status == 200, "health endpoint failed")
                        break
                    except (URLError, TimeoutError):
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("temporary API did not start")
                        time.sleep(0.1)
                client = Client(base)
                password = secrets.token_urlsafe(24)
                alice = client.request("POST", "/auth/register", {"username": "smoke-alice", "password": password, "invite_code": invitation})
                bob = client.request("POST", "/auth/register", {"username": "smoke-bob", "password": secrets.token_urlsafe(24), "invite_code": invitation})
                config_path = scratch_path / "agent.json"
                with patch("getpass.getpass", return_value=password):
                    check(agent_main(["--config", str(config_path), "login", "--server", base,
                                      "--name", "smoke-simulation", "--username", "smoke-alice"]) == 0,
                          "agent interactive login/enrollment failed")
                config = load_config(config_path)
                raw_config = config_path.read_text()
                check(password not in raw_config and alice["access_token"] not in raw_config,
                      "configuration contains account credentials")
                check(config_path.stat().st_mode & 0o077 == 0, "configuration permissions are too broad")
                run_args = ["--config", str(config_path), "run", "--simulate", "--once"]
                check(agent_main(run_args) == 0, "first simulated report failed")
                view = client.request("GET", "/servers", token=alice["access_token"])["servers"]
                check(len(view) == 1 and view[0]["gpus"], "dashboard did not receive GPU rows")
                check(all(g["state"] != "idle" for g in view[0]["gpus"]), "GPU green before confirmation")
                check(client.request("GET", "/servers", token=bob["access_token"])["servers"] == [],
                      "cross-account list leaked servers")
                try:
                    client.request("DELETE", f"/servers/{config['server_id']}", token=bob["access_token"])
                    raise AssertionError("cross-account unbind succeeded")
                except APIError as error:
                    check(error.status == 404, "cross-account unbind must return 404")
                time.sleep(2.1)
                check(agent_main(run_args) == 0, "confirmation report failed")
                view = client.request("GET", "/servers", token=alice["access_token"])["servers"][0]
                check(any(g["state"] == "idle" for g in view["gpus"]), "idle did not confirm")
                check(any(g["state"] == "busy" for g in view["gpus"]), "busy GPU missing")
                check(any(g["state"] == "uncertain" for g in view["gpus"]), "occupied GPU missing")
                check(all("utilization_avg_5m_percent" in g for g in view["gpus"]), "average missing from response")
                new_password = secrets.token_urlsafe(24)
                changed = client.request("POST", "/auth/password", {
                    "current_password": password, "new_password": new_password,
                }, token=alice["access_token"])
                try:
                    client.request("GET", "/servers", token=alice["access_token"])
                    raise AssertionError("old account token survived password rotation")
                except APIError as error:
                    check(error.status == 401, "old token must return 401")
                alice = changed
                check(agent_main(run_args) == 0, "password rotation revoked the device unexpectedly")
                time.sleep(3.1)
                view = client.request("GET", "/servers", token=alice["access_token"])["servers"][0]
                check(view["offline"] and all(g["state"] == "unknown" for g in view["gpus"]),
                      "stale data retained an actionable state")
                client.request("DELETE", f"/servers/{config['server_id']}", token=alice["access_token"])
                check(agent_main(run_args) == 3, "revoked device did not stop reporting")
                print("PASS: real HTTP registration/enrollment, private credentials, rolling states, account isolation, password/session rotation, device continuity, stale override, and device revocation.")
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read()[-6000:], file=sys.stderr)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
