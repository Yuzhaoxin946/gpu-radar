import argparse
from contextlib import contextmanager
import fcntl
import getpass
import json
import logging
import math
import os
from pathlib import Path
import random
import signal
import socket
import sys
import threading
import time

from .api import APIError, Client
from .collector import collect_bounded, simulated_report
from .config import default_config_path, load_config, normalize_server, write_config
from .server_clock import ServerClock

LOG = logging.getLogger("gpu-radar-agent")


def login(args) -> int:
    path = Path(args.config).expanduser()
    if path.exists():
        raise ValueError("This config is already bound. Use status to inspect it, or logout before enrolling a different device.")
    server = normalize_server(args.server, args.allow_insecure_http)
    username = args.username or input("App account username: ").strip()
    if not username:
        raise ValueError("Username cannot be empty.")
    password = getpass.getpass("App account password: ")
    client = Client(server)
    session = client.request("POST", "/auth/login", {"username": username, "password": password})
    del password
    token = session["access_token"]
    try:
        device = client.request("POST", "/agents/enroll", {"name": args.name or socket.gethostname(), "hostname": socket.gethostname()}, token=token)
        write_config(path, {
            "version": 1, "server": server, "server_id": device["server_id"], "device_token": device["device_token"],
            "name": device["name"], "allow_insecure_http": args.allow_insecure_http,
        })
    finally:
        try:
            client.request("POST", "/auth/logout", token=token)
        except APIError:
            LOG.warning("Could not revoke the transient account session. It was not saved locally and will expire on the server.")
    print(f"Bound '{device['name']}' to your account. Device configuration saved to {path} (0600).")
    print("Start reporting with gpu-radar-agent run using the same --config path.")
    return 0


@contextmanager
def run_lock(path: Path):
    """Prevent simultaneous real/simulated reporting with the same identity."""
    lock_path = str(path.expanduser()) + ".lock"
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another agent is already running with this config.") from None
        yield
    finally:
        os.close(fd)


def run_loop(args, config: dict, stop: threading.Event) -> int:
    client = Client(config["server"], timeout=args.request_timeout)
    server_clock = ServerClock()
    failures = 0
    if args.simulate:
        LOG.warning("SIMULATION enabled: all reported GPU data is synthetic and visibly labeled.")
    LOG.info("Reporting to %s as %s every %.1f seconds.", config["server"], config["name"], args.interval)
    while not stop.is_set():
        started = time.monotonic()
        try:
            server_clock.ensure_synced(client)
            # Timestamp the start, conservatively, rather than the end of
            # collection. The collector's host-clock timestamp is diagnostic.
            sampled_at = server_clock.timestamp()
        except APIError as error:
            failures += 1
            LOG.warning("Backend time synchronization failed; no sample sent: %s", error)
            if args.once:
                return 1
            stop.wait(retry_delay(args.interval, failures))
            continue
        report = simulated_report() if args.simulate else collect_bounded(args.collection_timeout)
        report["sampled_at"] = sampled_at
        try:
            client.request("POST", "/agent/report", report, token=config["device_token"])
            failures = 0
            if report["collection_status"] == "error":
                LOG.warning("Collection error reported: %s", report["error"])
            elif args.once:
                LOG.info("Reported %s GPU(s).", len(report["gpus"]))
        except APIError as error:
            if error.status in {401, 403}:
                LOG.error("Device credential rejected. Reporting stopped. Unbind/re-enroll the device to resume.")
                return 3
            if error.status is not None and 400 <= error.status < 500 and error.status not in {408, 429}:
                LOG.error("Report rejected: %s Check server/agent versions and settings.", error)
                return 4
            failures += 1
            LOG.warning("Report delivery failed: %s", error)
            if args.once:
                return 1
        if args.once:
            return 0 if report["collection_status"] == "ok" else 2
        if failures:
            # Drop failed samples and collect a fresh one on retry: no stale queue
            # may turn a now-busy card green after reconnecting.
            delay = retry_delay(args.interval, failures)
        else:
            delay = max(0.0, args.interval - (time.monotonic() - started))
        stop.wait(delay)
    return 0


def retry_delay(interval, failures):
    return min(60.0, interval * 2 ** min(failures - 1, 8)) * random.uniform(0.8, 1.2)


def run(args) -> int:
    values = (args.interval, args.collection_timeout, args.request_timeout)
    if not all(math.isfinite(value) for value in values) or args.interval < 1 or args.collection_timeout <= 0 or args.request_timeout <= 0:
        raise ValueError("Interval must be at least 1 second and timeouts must be positive.")
    config = load_config(Path(args.config))
    stop = threading.Event()
    previous = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, lambda *_: stop.set())
    try:
        with run_lock(Path(args.config)):
            return run_loop(args, config, stop)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def status(args) -> int:
    config = load_config(Path(args.config))
    print(json.dumps({"server": config["server"], "server_id": config["server_id"], "name": config["name"],
                      "config": str(Path(args.config).expanduser()), "bound_locally": True}, indent=2))
    print("This inspects local binding only; view last-seen and device validity in the Mac app.")
    return 0


def collect_command(args) -> int:
    """Emit one real diagnostic sample without credentials or network access."""
    if not math.isfinite(args.collection_timeout) or args.collection_timeout <= 0:
        raise ValueError("Collection timeout must be positive and finite.")
    report = collect_bounded(args.collection_timeout)
    print(json.dumps(report, allow_nan=False))
    return 0 if report["collection_status"] == "ok" else 2


def logout(args) -> int:
    path = Path(args.config).expanduser()
    if not path.exists():
        print("No local device credential exists.")
        return 0
    # Refuse to delete credentials while a worker is still using them.
    with run_lock(path):
        load_config(path)
        path.unlink()
    print("Local device credential removed. Delete/unbind this server in the Mac app to revoke its server-side token and record.")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Account-bound GPU Radar Linux agent")
    root.add_argument("--config", default=str(default_config_path()), help="Device credential file (default: %(default)s)")
    subparsers = root.add_subparsers(dest="command")
    # add_subparsers(required=...) was added in Python 3.7.
    subparsers.required = True
    commands = {}
    for name, function, help_text in (
        ("login", login, "Sign in with an App account and enroll this server"),
        ("run", run, "Collect and report continuously"),
        ("status", status, "Inspect local binding without revealing the device credential"),
        ("collect", collect_command, "Print one real GPU sample as JSON; no account or network required"),
        ("logout", logout, "Remove local credentials; unbind in Mac app to revoke remotely"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        # Accept --config on either side of the subcommand.
        sub.add_argument("--config", default=argparse.SUPPRESS, help="Device credential file")
        sub.set_defaults(function=function)
        commands[name] = sub
    commands["login"].add_argument("--server", required=True, help="Backend URL, e.g. https://radar.example.org")
    commands["login"].add_argument("--name", help="Display name for this GPU server")
    commands["login"].add_argument("--username", help="App account username; prompted when omitted")
    commands["login"].add_argument("--allow-insecure-http", action="store_true", help="Explicitly allow non-local unencrypted HTTP for trusted test networks")
    commands["run"].add_argument("--interval", type=float, default=5, help="Sample interval seconds (default: 5)")
    commands["run"].add_argument("--collection-timeout", type=float, default=8, help="Maximum collection worker seconds")
    commands["run"].add_argument("--request-timeout", type=float, default=10, help="Maximum API request seconds")
    commands["run"].add_argument("--simulate", action="store_true", help="EXPLICIT DEMO: send labeled synthetic GPU samples")
    commands["run"].add_argument("--once", action="store_true", help="Send one sample and exit (smoke test)")
    commands["collect"].add_argument("--collection-timeout", type=float, default=8, help="Maximum collection worker seconds")
    return root


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parser().parse_args(argv)
    try:
        return args.function(args)
    except KeyboardInterrupt:
        return 130
    except FileNotFoundError:
        LOG.error("Device configuration not found; use login first.")
        return 1
    except (ValueError, APIError, OSError, EOFError) as error:
        LOG.error("%s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
