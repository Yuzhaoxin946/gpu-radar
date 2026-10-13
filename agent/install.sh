#!/usr/bin/env bash
# GPU Radar installer. The whole function is parsed before a piped script runs.
set -eu
main() {
  command -v python3 >/dev/null 2>&1 || { echo 'GPU Radar requires Python 3.6 or newer.' >&2; return 1; }
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 6) else 1)' || {
    echo 'GPU Radar requires Python 3.6 or newer; system Python was not changed.' >&2; return 1;
  }
  python3 - "$@" <<'PYTHON_INSTALLER'
"""Self-contained, account-bound Linux installer; Python 3.6+ stdlib only."""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SERVER = "https://123.56.135.166"
ARTIFACT_URL = "https://github.com/Yuzhaoxin946/gpu-radar/releases/download/v0.2.4/gpu-radar-agent.pyz"
ALIYUN_ARTIFACT_URL = SERVER + "/agent/releases/0.1.1/gpu-radar-agent.pyz"
ARTIFACT_REDIRECT_HOSTS = frozenset({"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"})
INSTALLER_USER_AGENT = "gpu-radar-installer/0.2.4"
ARTIFACT_SHA256 = "05e6e6fb2f210f7897eb04f025896ebd7f2868f85beb122e0bf37f3c5ccab2ba"
LEGACY_SERVICE = "gpu-radar-agent.service"
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024


class InstallError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_artifact_url(value):
    try:
        parsed = urlsplit(value)
        allowed = (parsed.scheme == "https" and parsed.hostname in ARTIFACT_REDIRECT_HOSTS
                   and parsed.port in {None, 443} and parsed.username is None
                   and parsed.password is None and not parsed.fragment
                   and not any(ord(char) < 32 or ord(char) == 127 for char in value))
    except ValueError:
        allowed = False
    if not allowed:
        raise InstallError("Artifact redirect refused: only approved GitHub HTTPS hosts on port 443 are allowed.")


class ArtifactRedirect(HTTPRedirectHandler):
    """Public downloads only; never carry credentials to signed asset URLs."""
    # Python 3.6 lacks the 308 alias present in newer urllib versions.
    http_error_308 = HTTPRedirectHandler.http_error_302

    def __init__(self):
        self.redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_artifact_url(newurl)
        self.redirect_count += 1
        if self.redirect_count > 5:
            raise InstallError("Artifact download exceeded five redirects.")
        if req.get_method() != "GET" or code not in {301, 302, 303, 307, 308}:
            raise InstallError("Artifact redirect refused: unexpected request method or redirect status.")
        # Rebuild headers from scratch: no Authorization, cookies, or origin headers.
        return Request(newurl, headers={"User-Agent": INSTALLER_USER_AGENT}, method="GET")


def command(args, check=True, **kwargs):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, timeout=40, **kwargs)
    if check and result.returncode:
        # Never echo arbitrary child stderr: a remote response could contain secrets.
        raise InstallError("Command failed: {} (exit {}).".format(" ".join(args[:3]), result.returncode))
    return result


def download(destination, mirror=None):
    if mirror not in {None, "aliyun"}:
        raise InstallError("Unknown download mirror.")
    url = ALIYUN_ARTIFACT_URL if mirror == "aliyun" else ARTIFACT_URL
    if mirror is None:
        validate_artifact_url(url)
    request = Request(url, headers={"User-Agent": INSTALLER_USER_AGENT})
    redirect_handler = NoRedirect() if mirror == "aliyun" else ArtifactRedirect()
    digest = hashlib.sha256()
    size = 0
    with build_opener(redirect_handler).open(request, timeout=30) as response:
        if response.status != 200:
            raise InstallError("Agent download did not return HTTP 200.")
        with open(str(destination), "xb") as output:
            os.chmod(str(destination), 0o600)
            while True:
                data = response.read(65536)
                if not data:
                    break
                size += len(data)
                if size > MAX_ARTIFACT_BYTES:
                    raise InstallError("Agent download exceeds the size limit.")
                output.write(data)
                digest.update(data)
            output.flush()
            os.fsync(output.fileno())
    if digest.hexdigest() != ARTIFACT_SHA256:
        raise InstallError("Agent checksum mismatch; installed files were not replaced.")


def validate_machine_id(value):
    if not re.fullmatch(r"[0-9a-f]{32}", value) or value == "0" * 32:
        raise InstallError("/etc/machine-id must contain a nonzero 32-character lowercase hexadecimal machine ID. Ask the administrator to fix it; no hostname fallback is used.")
    return value


def read_machine_id():
    try:
        with open("/etc/machine-id", "r", encoding="ascii") as source:
            value = source.read(128).strip()
    except (OSError, UnicodeError):
        raise InstallError("Cannot read a valid /etc/machine-id. Ask the administrator to fix it before installing.")
    return validate_machine_id(value)


def host_paths(home, machine_id):
    validate_machine_id(machine_id)
    unit_dir = home / ".config/systemd/user"
    service = "gpu-radar-agent-" + machine_id + ".service"
    return {
        "package": home / ".local/share/gpu-radar/hosts" / machine_id,
        "config_dir": home / ".config/gpu-radar/hosts" / machine_id,
        "unit_dir": unit_dir,
        "unit": unit_dir / service,
        "service": service,
    }


def refuse_legacy_installation(home):
    # A shared NFS home gives no reliable indication which host owns this old identity.
    legacy = (home / ".config/gpu-radar/agent.json",
              home / ".config/systemd/user" / LEGACY_SERVICE,
              home / ".config/systemd/user/default.target.wants" / LEGACY_SERVICE)
    if any(path.exists() or path.is_symlink() for path in legacy):
        raise InstallError("A legacy GPU Radar binding or service exists in this HOME. It has been preserved unchanged; automatic migration could reuse another server's identity on a shared home. Contact the maintainer for a host-verified migration before installing. Do not delete or copy its device credentials.")


def check_platform():
    if sys.platform != "linux":
        raise InstallError("Run this installer on a Linux GPU server.")
    if not shutil.which("nvidia-smi"):
        raise InstallError("nvidia-smi is missing from PATH. Install/fix the NVIDIA driver first.")


def current_account():
    # sudo may preserve HOME/SUDO_USER from the caller. Installation belongs to
    # the effective Linux user, never to a different user's inherited home.
    try:
        account = pwd.getpwuid(os.geteuid())
    except KeyError:
        raise InstallError("The current Linux user has no passwd entry; configure an account home before installing.")
    if not Path(account.pw_dir).is_absolute() or Path(account.pw_dir) == Path("/"):
        raise InstallError("The current Linux user needs an absolute, dedicated home directory.")
    os.environ["HOME"] = account.pw_dir
    return account


def preflight(artifact):
    result = command([sys.executable, str(artifact), "collect", "--collection-timeout", "8"], check=False)
    if result.returncode:
        raise InstallError("Real GPU collection failed. Run nvidia-smi to check driver access; existing service was not stopped.")
    try:
        report = json.loads(result.stdout)
    except ValueError:
        raise InstallError("GPU collection returned invalid data.")
    if report.get("collection_status") != "ok" or not report.get("gpus"):
        raise InstallError("No usable NVIDIA GPU was detected; existing service was not stopped.")
    request = Request(SERVER + "/api/v1/health", headers={"Accept": "application/json"})
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        if response.status != 200:
            raise InstallError("GPU Radar HTTPS health check failed.")
        body = json.loads(response.read(65536))
        if body.get("status") != "ok":
            raise InstallError("GPU Radar backend is not healthy.")
    print("Preflight passed: {} real GPU(s), verified download, HTTPS reachable.".format(len(report["gpus"])))


def safe_directory(home, path):
    """Only use user-owned, non-symlink directories under a protected home."""
    if not home.is_absolute() or path != home and home not in path.parents:
        raise InstallError("Installation paths must be inside an absolute HOME.")
    # Refuse symlink ancestors as well as leaf paths before touching credentials.
    for ancestor in list(home.parents)[::-1] + [home]:
        if ancestor.is_symlink():
            raise InstallError("HOME must not contain symlink components: {}".format(ancestor))
    targets = [home]
    targets.extend(home.joinpath(*path.relative_to(home).parts[:i])
                   for i in range(1, len(path.relative_to(home).parts) + 1))
    for target in targets:
        if not target.exists() and not target.is_symlink():
            target.mkdir(mode=0o700, exist_ok=True)
        metadata = target.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise InstallError("Unsafe directory (requires your ownership, no symlink/group/world write): {}".format(target))


def safe_file(path):
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_nlink != 1:
            raise InstallError("Refusing unsafe file (symlink, hardlink or wrong owner): {}".format(path))


def atomic_write(path, data, mode=0o600):
    safe_file(path)
    fd, temporary = tempfile.mkstemp(prefix=".install-", dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def existing_config(path, artifact, username, name, machine_id):
    safe_file(path)
    if not path.exists():
        return None
    # Reuse the agent's own strict ownership/permissions/format validation.
    command([sys.executable, str(artifact), "status", "--config", str(path)])
    with path.open() as source:
        config = json.load(source)
    if config.get("server", "").rstrip("/") != SERVER or config.get("allow_insecure_http"):
        raise InstallError("Existing config points to a different backend; it was kept unchanged.")
    if name is not None and config.get("name") != name:
        raise InstallError("Existing binding has a different server name; use its current name or manage the binding in the Mac app.")
    if config.get("machine_id", machine_id) != machine_id:
        raise InstallError("Existing device config belongs to a different machine ID; it was kept unchanged.")
    account = config.get("account_username")
    if account and username and account != username:
        raise InstallError("Existing binding belongs to a different App account; it was kept unchanged.")
    print("Keeping existing device binding for '{}' (no duplicate enrollment).".format(config["name"]))
    if username and not account:
        print("Existing config has no account label. --username applies only to new enrollment; the existing account binding is kept.")
    return config


def linger_enabled(username):
    result = command(["loginctl", "show-user", username, "--property=Linger", "--value"], check=False)
    return result.returncode == 0 and result.stdout.strip() == "yes"


def open_tty():
    try:
        terminal = io.TextIOWrapper(io.FileIO("/dev/tty", "r+"), encoding="utf-8", line_buffering=True)
        if not terminal.isatty():
            terminal.close()
            raise OSError("not a terminal")
        return terminal
    except OSError:
        raise InstallError("An interactive SSH terminal is required. Reconnect with ssh -t, then rerun this command.")


def ensure_linger(username):
    if linger_enabled(username):
        return
    command(["loginctl", "--no-ask-password", "enable-linger", username], check=False)
    if not linger_enabled(username) and os.geteuid() != 0 and shutil.which("sudo"):
        # Try authorized passwordless sudo first; otherwise ask explicitly in the TTY.
        result = command(["sudo", "-n", "loginctl", "enable-linger", username], check=False)
        if result.returncode:
            with open_tty() as terminal:
                terminal.write("Reboot startup needs administrator permission. Try sudo for 'loginctl enable-linger'? [y/N] ")
                terminal.flush()
                if terminal.readline().strip().lower() in {"y", "yes"}:
                    subprocess.run(["sudo", "loginctl", "enable-linger", username], stdin=terminal)
    if not linger_enabled(username):
        if os.geteuid() == 0:
            raise InstallError("Reboot startup was NOT enabled for root. Check that systemd-logind and the root user manager are available; no account was enrolled.")
        raise InstallError("Reboot startup was NOT enabled. Ask an administrator to run: sudo loginctl enable-linger {} . Then rerun the installer; no account was enrolled.".format(shlex.quote(username)))


def systemd_preflight(username, scope="user"):
    if scope == "system":
        if os.geteuid() != 0 or not shutil.which("systemctl"):
            raise InstallError("A root account and systemd are required for a system service.")
        if command(["systemctl", "--system", "show-environment"], check=False).returncode:
            raise InstallError("The system service manager is unavailable (common inside containers).")
        return
    for executable in ("systemctl", "loginctl"):
        if not shutil.which(executable):
            raise InstallError("{} is required for persistent user service installation (including root). Containers without systemd need an existing process manager instead.".format(executable))
    ensure_linger(username)
    runtime = Path("/run/user") / str(os.geteuid())
    # Do not reuse the invoking user's bus when sudo preserves its environment.
    os.environ["XDG_RUNTIME_DIR"] = str(runtime)
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + str(runtime / "bus")
    for attempt in range(6):
        if command(["systemctl", "--user", "show-environment"], check=False).returncode == 0:
            return
        if attempt < 5:
            time.sleep(1)
    raise InstallError("Your systemd user manager is unavailable. Log out and reconnect with SSH after enabling linger, then rerun; no account was enrolled.")


def enroll(artifact, config, username, name, machine_id):
    with open_tty() as terminal:
        if not username:
            terminal.write("GPU Radar App username: ")
            terminal.flush()
            username = terminal.readline().strip().lower()
            validate_username(username)
        # The existing CLI uses getpass on this controlling terminal: never echo a password.
        args = [sys.executable, str(artifact), "login", "--config", str(config),
                "--server", SERVER, "--username", username, "--name", name]
        result = subprocess.run(args, stdin=terminal)
        if result.returncode:
            raise InstallError("Account login/enrollment failed; the existing service was not changed.")
    with config.open() as source:
        data = json.load(source)
    data["account_username"] = username
    data["machine_id"] = machine_id
    atomic_write(config, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


def systemd_escape(value):
    if any(ord(char) < 32 for char in value):
        raise InstallError("Executable path contains invalid characters.")
    return value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')


def unit_text(machine_id, scope="user", home=None):
    validate_machine_id(machine_id)
    # Escape systemd specifiers and argument syntax, retaining the selected Python.
    executable = systemd_escape(sys.executable)
    if scope not in {"user", "system"}:
        raise InstallError("Unknown service scope.")
    if scope == "system" and (home is None or not Path(home).is_absolute()):
        raise InstallError("A system service requires an absolute account home.")
    home_path = "%h" if scope == "user" else systemd_escape(str(home))
    target = "default.target" if scope == "user" else "multi-user.target"
    gpu_directory = str(Path(shutil.which("nvidia-smi") or "/usr/bin/nvidia-smi").parent)
    search_path = systemd_escape(gpu_directory + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    return ("""[Unit]
Description=GPU Radar GPU telemetry agent for host {machine_id}
ConditionHost={machine_id}
After=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=10

[Service]
Type=simple
WorkingDirectory="{home}/.local/share/gpu-radar/hosts/{machine_id}"
Environment="PATH={search_path}"
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=\"{python}\" \"{home}/.local/share/gpu-radar/hosts/{machine_id}/gpu-radar-agent.pyz\" run --config \"{home}/.config/gpu-radar/hosts/{machine_id}/agent.json\" --interval 5
Restart=on-failure
RestartPreventExitStatus=3 4
RestartSec=15
TimeoutStopSec=25
KillMode=control-group
NoNewPrivileges=true
UMask=0077
SyslogIdentifier=gpu-radar-agent

[Install]
WantedBy={target}
""".format(python=executable, search_path=search_path, machine_id=machine_id, home=home_path, target=target)).encode("utf-8")


def activate(artifact, installed, unit, config, machine_id, scope="user", home=None):
    service = "gpu-radar-agent-" + validate_machine_id(machine_id) + ".service"
    if unit.name != service:
        raise InstallError("Service path does not match this machine ID.")
    old_files = {path: path.read_bytes() if path.exists() else None for path in (installed, unit)}
    active = command(["systemctl", "--" + scope, "is-active", "--quiet", service], check=False).returncode == 0
    enabled = command(["systemctl", "--" + scope, "is-enabled", "--quiet", service], check=False).returncode == 0
    changed = False
    stopped = False
    try:
        if active:
            stopped = True
            command(["systemctl", "--" + scope, "stop", service])
        # Verify the real device token and actual report before replacing installed files.
        report = command([sys.executable, str(artifact), "run", "--config", str(config), "--once"], check=False)
        if report.returncode:
            raise InstallError("First real report failed (exit {}). Existing installation will be restored; binding is preserved for retry.".format(report.returncode))
        changed = True
        atomic_write(installed, artifact.read_bytes())
        atomic_write(unit, unit_text(machine_id, scope, home))
        command(["systemctl", "--" + scope, "daemon-reload"])
        command(["systemctl", "--" + scope, "enable", service])
        command(["systemctl", "--" + scope, "restart", service])
        time.sleep(2)
        command(["systemctl", "--" + scope, "is-active", "--quiet", service])
        command(["systemctl", "--" + scope, "is-enabled", "--quiet", service])
    except BaseException:
        if changed:
            command(["systemctl", "--" + scope, "stop", service], check=False)
            if not enabled:
                command(["systemctl", "--" + scope, "disable", service], check=False)
            for path, content in old_files.items():
                if content is None:
                    if path.exists():
                        path.unlink()
                else:
                    atomic_write(path, content)
            command(["systemctl", "--" + scope, "daemon-reload"], check=False)
            if enabled:
                command(["systemctl", "--" + scope, "enable", service], check=False)
        if active and (stopped or changed):
            restored = command(["systemctl", "--" + scope, "start", service], check=False)
            if restored.returncode:
                print("WARNING: previous service could not restart; inspect systemctl --" + scope + " status " + service, file=sys.stderr)
        raise


def system_unit_directory():
    return Path("/etc/systemd/system")


def choose_background(requested, username, paths, home):
    """Keep existing supervision; only a fresh auto install may fall back."""
    marker = paths["package"] / "background-mode"
    safe_file(marker)
    existing = set()
    if marker.exists():
        value = marker.read_text().strip()
        if value not in {"user", "system", "process"}:
            raise InstallError("Invalid background-mode marker; existing installation was preserved.")
        existing.add(value)
    if paths["unit"].exists() or paths["unit"].is_symlink():
        existing.add("user")
    system_unit = system_unit_directory() / paths["service"]
    if os.geteuid() == 0 and (system_unit.exists() or system_unit.is_symlink()):
        existing.add("system")
    if len(existing) > 1:
        raise InstallError("Multiple background modes exist for this host; resolve them before installing to avoid duplicate collectors.")
    if existing:
        mode = next(iter(existing))
        if requested == "process" and mode != "process" or requested == "systemd" and mode == "process":
            raise InstallError("Existing background mode is {}. Automatic migration is refused; stop and remove the old installation before changing modes.".format(mode))
    else:
        mode = "process" if requested == "process" else "system" if os.geteuid() == 0 else "user"
    if mode != "process":
        try:
            systemd_preflight(username, scope=mode)
        except (InstallError, OSError, subprocess.SubprocessError) as error:
            if requested != "auto" or existing:
                raise
            print("System service unavailable; using a detached background process.")
            print("Automatic startup after host/container restart is NOT configured.")
            mode = "process"
    return mode


def process_running(artifact, config):
    status = command([sys.executable, str(artifact), "background", "status", "--config", str(config)], check=False)
    if status.returncode not in {0, 3}:
        raise InstallError("Cannot verify the existing background process; installation was preserved.")
    return status.returncode == 0


def activate_process(artifact, installed, config):
    previous = installed.read_bytes() if installed.exists() else None
    active = process_running(artifact, config)
    stopped = changed = False
    try:
        if active:
            command([sys.executable, str(artifact), "background", "stop", "--config", str(config)])
            stopped = True
        report = command([sys.executable, str(artifact), "run", "--config", str(config), "--once"], check=False)
        if report.returncode:
            raise InstallError("First real report failed (exit {}). Existing installation will be restored; binding is preserved for retry.".format(report.returncode))
        atomic_write(installed, artifact.read_bytes())
        changed = True
        command([sys.executable, str(installed), "background", "start", "--config", str(config)])
        if not process_running(installed, config):
            raise InstallError("The background process exited during startup; inspect its private log.")
    except BaseException:
        if changed:
            # A failed stop must not be followed by replacing the running image.
            command([sys.executable, str(artifact), "background", "stop", "--config", str(config)])
            if previous is None:
                installed.unlink()
            else:
                atomic_write(installed, previous)
        if active and stopped and previous is not None:
            restarted = command([sys.executable, str(installed), "background", "start", "--config", str(config)], check=False)
            if restarted.returncode:
                print("WARNING: previous background process could not restart; inspect its private log.", file=sys.stderr)
        raise


def validate_username(value):
    if not re.fullmatch(r"[a-z0-9_.-]{3,48}", value):
        raise InstallError("App username must be 3-48 letters, digits, underscores, dots or hyphens.")


def parser():
    result = argparse.ArgumentParser(description="Install GPU Radar in the current effective Linux user's home, including root or a sudo-selected user.")
    result.add_argument("--username", help="GPU Radar App account (prompted for a new binding if omitted)")
    result.add_argument("--name", help="Display name, default short hostname for a new binding")
    result.add_argument("--mirror", choices=["aliyun"], help="Download the same verified agent from Aliyun instead of GitHub Releases")
    result.add_argument("--background", choices=["auto", "systemd", "process"], default="auto",
                        help="auto: use systemd when available, otherwise a detached process; process mode needs manual restart after reboot")
    result.add_argument("--check", action="store_true", help="Check real GPU collection and HTTPS only; no enrollment or service changes")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    check_platform()
    machine_id = read_machine_id()
    if args.username:
        args.username = args.username.strip().lower()
        validate_username(args.username)
    name = args.name.strip() if args.name is not None else socket.gethostname().split(".")[0]
    if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
        raise InstallError("Server name must be 1-80 printable characters.")
    if args.name is not None:
        args.name = name
    if args.check:
        with tempfile.TemporaryDirectory(prefix="gpu-radar-check-") as temporary:
            artifact = Path(temporary) / "gpu-radar-agent.pyz"
            download(artifact, mirror=args.mirror)
            preflight(artifact)
        print("Check complete. No account enrolled; no installed files or services changed.")
        return 0
    account = current_account()
    home = Path(account.pw_dir)
    print("Installing for Linux user '{}' in {}.".format(account.pw_name, home))
    refuse_legacy_installation(home)
    paths = host_paths(home, machine_id)
    package, config_dir, unit_dir = paths["package"], paths["config_dir"], paths["unit_dir"]
    service = paths["service"]
    for directory in (package, config_dir):
        safe_directory(home, directory)
    os.chmod(str(package), 0o700)
    os.chmod(str(config_dir), 0o700)
    installed = package / "gpu-radar-agent.pyz"
    config = config_dir / "agent.json"
    unit = paths["unit"]
    lock = package / ".install.lock"
    mode_file = package / "background-mode"
    for path in (installed, config, unit, lock, mode_file, Path(str(config) + ".lock")):
        safe_file(path)
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError("Another GPU Radar installer is running for this user.")
        with tempfile.TemporaryDirectory(prefix=".stage-", dir=str(package)) as temporary:
            artifact = Path(temporary) / "gpu-radar-agent.pyz"
            download(artifact, mirror=args.mirror)
            preflight(artifact)
            binding = existing_config(config, artifact, args.username, args.name, machine_id)
            mode = choose_background(args.background, account.pw_name, paths, home)
            if mode == "system":
                unit_dir = system_unit_directory()
                unit = unit_dir / service
                safe_directory(Path("/"), unit_dir)
                safe_file(unit)
            elif mode == "user":
                safe_directory(home, unit_dir)
            # Check all destinations before obtaining a new remote device binding.
            destinations = (config_dir,) if mode == "process" else (config_dir, unit_dir)
            for directory in destinations:
                probe_fd, probe_path = tempfile.mkstemp(prefix=".write-check-", dir=str(directory))
                os.close(probe_fd)
                os.unlink(probe_path)
            if mode != "process":
                unit_text(machine_id, mode, home)
            if binding is None:
                enroll(artifact, config, args.username, name, machine_id)
            if mode == "process":
                activate_process(artifact, installed, config)
            else:
                activate(artifact, installed, unit, config, machine_id, scope=mode, home=home)
            if mode == "user" and not linger_enabled(account.pw_name):
                raise InstallError("Service is running, but Linger is not enabled; reboot persistence could not be confirmed.")
            atomic_write(mode_file, (mode + "\n").encode("ascii"))
        if mode == "process":
            print("Installed: reporting every 5 seconds; detached background process running.")
            print("The process is detached from SSH. Automatic startup after host/container restart is NOT configured.")
            prefix = "{} {} background ".format(shlex.quote(sys.executable), shlex.quote(str(installed)))
            suffix = " --config " + shlex.quote(str(config))
            for action in ("status", "start", "stop"):
                print(action.capitalize() + ": " + prefix + action + suffix)
            print("Logs: " + str(config) + ".background.log")
        else:
            print("Installed: reporting every 5 seconds; service active and enabled" + ("; Linger=yes." if mode == "user" else "."))
            print("The agent continues after SSH logout and starts after reboot.")
            print("Status: systemctl --{} status {}".format(mode, service))
            print("Logs:   journalctl {}-u {} -n 30 --no-pager".format("--user " if mode == "user" else "", service))
        print("First report accepted. Allow 5 minutes of continuous samples for color classification.")
        return 0
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInstallation interrupted; rerun to reuse any saved binding.", file=sys.stderr)
        sys.exit(130)
    except (InstallError, OSError, ValueError, subprocess.SubprocessError) as error:
        # No account password/device token is included in exception details.
        print("GPU Radar installation stopped: {}".format(error), file=sys.stderr)
        sys.exit(1)
PYTHON_INSTALLER
}
main "$@"
