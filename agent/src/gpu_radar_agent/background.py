"""A detached, single-instance Linux worker for hosts without a user manager.

There is deliberately no supervisor and no promise of restart persistence. Stop
requests are consumed by the verified worker itself: never signal a bare PID
which the kernel might have reused between inspection and kill().
"""

from contextlib import contextmanager
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
import zipfile

from .config import load_config, write_config

LOG = logging.getLogger("gpu-radar-agent")
START_TIMEOUT = 8.0
STOP_TIMEOUT = 25.0


def paths(config_path):
    config_path = Path(config_path).expanduser().resolve()
    return {name: Path(str(config_path) + ".background." + suffix)
            for name, suffix in (("metadata", "json"), ("request", "stop"),
                                 ("lock", "lock"), ("log", "log"))}


def _require_linux():
    if not sys.platform.startswith("linux") or not Path("/proc/self/stat").exists():
        raise ValueError("Background management requires Linux with /proc mounted.")


def _private_open(path, flags):
    fd = os.open(str(path), flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError("Background state must be a regular file owned by this user.")
        if info.st_mode & 0o077:
            if flags & (os.O_WRONLY | os.O_RDWR):
                os.fchmod(fd, 0o600)
            else:
                raise ValueError("Background state must have permission 600.")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_private(path):
    try:
        fd = _private_open(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        # Corrupt state must never cause an unbounded read or a signal.
        text = handle.read(65537)
    if len(text) > 65536:
        raise ValueError("Background state is too large.")
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError("Invalid background state; no process was stopped.") from None
    if not isinstance(value, dict):
        raise ValueError("Invalid background state; no process was stopped.")
    return value


@contextmanager
def management_lock(config_path, deadline=None):
    state = paths(config_path)
    state["lock"].parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = _private_open(state["lock"], os.O_WRONLY | os.O_CREAT)
    try:
        # Serialize callers without holding the reporting lock or waiting
        # forever for an interrupted manager. Stop includes this wait in its
        # overall 25-second deadline.
        if deadline is None:
            deadline = time.monotonic() + STOP_TIMEOUT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError("Another background management command is still running; retry shortly.") from None
                time.sleep(0.05)
        yield state
    finally:
        os.close(fd)


def _process_identity(pid):
    if type(pid) is not int or pid <= 1:
        return None
    root = Path("/proc") / str(pid)
    try:
        fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] in {"Z", "X"}:
            return None
        start_ticks = int(fields[19])
        uid = None
        for line in (root / "status").read_text().splitlines():
            if line.startswith("Uid:"):
                uid = int(line.split()[2])  # effective UID
                break
        if uid is None:
            return None
        command = (root / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        # /proc files are separate snapshots. The process may have become a
        # zombie (with empty cmdline) since the first stat read.
        final_fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
        if final_fields[0] in {"Z", "X"} or int(final_fields[19]) != start_ticks:
            return None
        return {"pid": pid, "start_ticks": start_ticks, "uid": uid,
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "command": [os.fsdecode(item) for item in command]}
    except (FileNotFoundError, ProcessLookupError, IndexError, ValueError):
        return None


def _command_prefix():
    candidate = Path(sys.argv[0]).expanduser().absolute()
    if candidate.is_file() and zipfile.is_zipfile(str(candidate)):
        return [sys.executable, str(candidate)]
    return [sys.executable, "-m", "gpu_radar_agent"]


def _command(config_path):
    return _command_prefix() + ["run", "--config", str(config_path), "--interval", "5", "--background-managed"]


def _worker_command(command, config_path):
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        return False
    suffix = ["run", "--config", str(config_path), "--interval", "5", "--background-managed"]
    if command[-len(suffix):] != suffix:
        return False
    prefix = command[:-len(suffix)]
    # A freshly downloaded installer may inspect the currently installed pyz.
    # Compare its recorded argv to /proc, not to the caller's artifact path.
    return (len(prefix) == 3 and Path(prefix[0]).is_absolute() and
            prefix[1:] == ["-m", "gpu_radar_agent"]) or (
                len(prefix) == 2 and Path(prefix[0]).is_absolute() and
                Path(prefix[1]).is_absolute() and prefix[1].endswith(".pyz"))


def _matches(metadata, config_path):
    if not metadata:
        return False
    if (metadata.get("version") != 1 or metadata.get("config") != str(config_path)
            or metadata.get("uid") != os.geteuid()
            or not isinstance(metadata.get("nonce"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", metadata["nonce"])):
        raise ValueError("Invalid background identity; no process was stopped.")
    current = _process_identity(metadata.get("pid"))
    if current is None:
        return False
    # An old boot or a recycled PID is stale state, never a target to stop.
    if not all(current.get(key) == metadata.get(key) for key in
               ("pid", "start_ticks", "uid", "boot_id")):
        return False
    if (current["command"] != metadata.get("command") or
            not _worker_command(current["command"], config_path)):
        raise ValueError("Recorded process is alive but cannot be verified as this agent; no process was stopped.")
    return True


def _unlink(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def start(config_path):
    _require_linux()
    config_path = Path(config_path).expanduser().resolve()
    load_config(config_path)
    with management_lock(config_path) as state:
        metadata = _read_private(state["metadata"])
        if _matches(metadata, config_path):
            print("Background agent is already running (PID {}).".format(metadata["pid"]))
            return 0
        # Also refuse a foreground/systemd worker using this device identity.
        from .cli import run_lock
        with run_lock(config_path):
            _unlink(state["metadata"])
            _unlink(state["request"])
        environment = os.environ.copy()
        if "-m" in _command_prefix():
            source = str(Path(__file__).resolve().parent.parent)
            environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
        worker = subprocess.Popen(_command(config_path), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  close_fds=True, start_new_session=True, env=environment)
        try:
            deadline = time.monotonic() + START_TIMEOUT
            first_seen = None
            while time.monotonic() < deadline:
                if worker.poll() is not None:
                    raise ValueError("Background agent exited during startup; inspect {}.".format(state["log"]))
                metadata = _read_private(state["metadata"])
                if _matches(metadata, config_path) and metadata["pid"] == worker.pid:
                    # The worker has acquired the reporting lock and installed
                    # its handlers; give immediate failures time to surface.
                    if first_seen is None:
                        first_seen = time.monotonic()
                    elif time.monotonic() - first_seen >= 0.5:
                        print("Background agent started (PID {}). Log: {}".format(worker.pid, state["log"]))
                        print("It survives SSH logout; restart it after a container/host restart.")
                        return 0
                time.sleep(0.05)
            raise ValueError("Background startup could not be confirmed; inspect {}.".format(state["log"]))
        except BaseException:
            # This is our still-unreaped child, so its PID cannot have been
            # reused. Never leave a late-starting worker behind on failure:
            # installers need to be able to restore the previous artifact.
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=5)
            raise


def status(config_path):
    _require_linux()
    config_path = Path(config_path).expanduser().resolve()
    with management_lock(config_path) as state:
        metadata = _read_private(state["metadata"])
        if _matches(metadata, config_path):
            print("Background agent is running (PID {}). Log: {}".format(metadata["pid"], state["log"]))
            return 0
        print("No managed background agent is running for this config.")
        return 3


def stop(config_path):
    _require_linux()
    config_path = Path(config_path).expanduser().resolve()
    deadline = time.monotonic() + STOP_TIMEOUT
    with management_lock(config_path, deadline=deadline) as state:
        metadata = _read_private(state["metadata"])
        if not _matches(metadata, config_path):
            print("No matching background agent is running; no process was stopped.")
            return 0
        # The worker checks this nonce itself. Even PID reuse immediately after
        # our identity check cannot send a signal to an unrelated process.
        request = {key: metadata[key] for key in ("nonce", "pid", "start_ticks", "boot_id")}
        write_config(state["request"], request)
        while time.monotonic() < deadline:
            # The nonce request is already addressed to the verified worker.
            # Here we only wait; during exit Linux can empty cmdline before
            # marking the process a zombie. That is not a reason to reject a
            # successful stop. A changed identity also ends this wait without
            # signaling or modifying the replacement process.
            current = _process_identity(metadata["pid"])
            if current is None or not all(current.get(key) == metadata[key] for key in
                                          ("pid", "start_ticks", "uid", "boot_id")):
                print("Background agent stopped.")
                return 0
            time.sleep(0.1)
        raise ValueError("Agent has received a stop request but has not exited within 25 seconds; inspect {}.".format(state["log"]))


class PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        return os.fdopen(_private_open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT),
                         "a", encoding="utf-8")


@contextmanager
def managed_worker(config_path, stop_event):
    """Run inside the existing reporting lock; publish only a ready worker."""
    _require_linux()
    config_path = Path(config_path).expanduser().resolve()
    state = paths(config_path)
    previous_hup = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    handler = PrivateRotatingFileHandler(str(state["log"]), maxBytes=1024 * 1024, backupCount=1)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger()
    logger.addHandler(handler)
    metadata = _process_identity(os.getpid())
    if metadata is None or metadata["command"] != _command(config_path):
        handler.close()
        logger.removeHandler(handler)
        raise ValueError("Cannot verify background worker identity.")
    metadata.update(version=1, config=str(config_path), nonce=uuid.uuid4().hex)
    finished = threading.Event()

    def watch_stop():
        while not finished.wait(0.1):
            try:
                request = _read_private(state["request"])
                if request and all(request.get(key) == metadata[key] for key in
                                   ("nonce", "pid", "start_ticks", "boot_id")):
                    stop_event.set()
                    return
            except (OSError, ValueError):
                # An invalid request never interrupts a worker or floods logs.
                pass

    watcher = threading.Thread(target=watch_stop, name="gpu-radar-background-stop")
    watcher.daemon = True
    try:
        write_config(state["metadata"], metadata)
        watcher.start()
        LOG.info("Detached background worker ready; PID %s.", metadata["pid"])
        yield
    finally:
        finished.set()
        if watcher.ident is not None:
            watcher.join(timeout=1)
        try:
            current = _read_private(state["metadata"])
            if current and current.get("nonce") == metadata["nonce"]:
                _unlink(state["metadata"])
                _unlink(state["request"])
        finally:
            logger.removeHandler(handler)
            handler.close()
            signal.signal(signal.SIGHUP, previous_hup)


def command(args):
    return {"start": start, "status": status, "stop": stop}[args.background_action](args.config)
