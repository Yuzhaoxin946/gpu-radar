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
ARTIFACT_URL = "https://github.com/Yuzhaoxin946/gpu-radar/releases/download/v0.2.2/gpu-radar-agent.pyz"
ALIYUN_ARTIFACT_URL = SERVER + "/agent/releases/0.1.0/gpu-radar-agent.pyz"
ARTIFACT_REDIRECT_HOSTS = frozenset({"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"})
INSTALLER_USER_AGENT = "gpu-radar-installer/0.2.2"
ARTIFACT_SHA256 = "5a83181728bcc9e932f0b1ec8639246795643759b48b7400b908b5945374f5bf"
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
    if os.getuid() == 0:
        raise InstallError("Run as your normal SSH user, without sudo. Only enabling reboot startup may request sudo.")
    if not shutil.which("nvidia-smi"):
        raise InstallError("nvidia-smi is missing from PATH. Install/fix the NVIDIA driver first.")


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
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
            raise InstallError("Unsafe directory (requires your ownership, no symlink/group/world write): {}".format(target))


def safe_file(path):
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
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
    if not linger_enabled(username) and shutil.which("sudo"):
        # Try authorized passwordless sudo first; otherwise ask explicitly in the TTY.
        result = command(["sudo", "-n", "loginctl", "enable-linger", username], check=False)
        if result.returncode:
            with open_tty() as terminal:
                terminal.write("Reboot startup needs administrator permission. Try sudo for 'loginctl enable-linger'? [y/N] ")
                terminal.flush()
                if terminal.readline().strip().lower() in {"y", "yes"}:
                    subprocess.run(["sudo", "loginctl", "enable-linger", username], stdin=terminal)
    if not linger_enabled(username):
        raise InstallError("Reboot startup was NOT enabled. Ask an administrator to run: sudo loginctl enable-linger {} . Then rerun the installer; no account was enrolled.".format(shlex.quote(username)))


def systemd_preflight(username):
    for executable in ("systemctl", "loginctl"):
        if not shutil.which(executable):
            raise InstallError("{} is required for persistent user service installation.".format(executable))
    ensure_linger(username)
    runtime = Path("/run/user") / str(os.getuid())
    for attempt in range(6):
        if runtime.is_dir() and runtime.stat().st_uid == os.getuid():
            os.environ["XDG_RUNTIME_DIR"] = str(runtime)
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + str(runtime / "bus")
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


def unit_text(machine_id):
    validate_machine_id(machine_id)
    # Escape systemd specifiers and argument syntax, retaining the selected Python.
    executable = systemd_escape(sys.executable)
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
WorkingDirectory=%h/.local/share/gpu-radar/hosts/{machine_id}
Environment="PATH={search_path}"
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=\"{python}\" \"%h/.local/share/gpu-radar/hosts/{machine_id}/gpu-radar-agent.pyz\" run --config \"%h/.config/gpu-radar/hosts/{machine_id}/agent.json\" --interval 5
Restart=on-failure
RestartPreventExitStatus=3 4
RestartSec=15
TimeoutStopSec=25
KillMode=control-group
NoNewPrivileges=true
UMask=0077
SyslogIdentifier=gpu-radar-agent

[Install]
WantedBy=default.target
""".format(python=executable, search_path=search_path, machine_id=machine_id)).encode("utf-8")


def activate(artifact, installed, unit, config, machine_id):
    service = "gpu-radar-agent-" + validate_machine_id(machine_id) + ".service"
    if unit.name != service:
        raise InstallError("Service path does not match this machine ID.")
    old_files = {path: path.read_bytes() if path.exists() else None for path in (installed, unit)}
    active = command(["systemctl", "--user", "is-active", "--quiet", service], check=False).returncode == 0
    enabled = command(["systemctl", "--user", "is-enabled", "--quiet", service], check=False).returncode == 0
    changed = False
    stopped = False
    try:
        if active:
            stopped = True
            command(["systemctl", "--user", "stop", service])
        # Verify the real device token and actual report before replacing installed files.
        report = command([sys.executable, str(artifact), "run", "--config", str(config), "--once"], check=False)
        if report.returncode:
            raise InstallError("First real report failed (exit {}). Existing installation will be restored; binding is preserved for retry.".format(report.returncode))
        changed = True
        atomic_write(installed, artifact.read_bytes())
        atomic_write(unit, unit_text(machine_id))
        command(["systemctl", "--user", "daemon-reload"])
        command(["systemctl", "--user", "enable", service])
        command(["systemctl", "--user", "restart", service])
        time.sleep(2)
        command(["systemctl", "--user", "is-active", "--quiet", service])
        command(["systemctl", "--user", "is-enabled", "--quiet", service])
    except BaseException:
        if changed:
            command(["systemctl", "--user", "stop", service], check=False)
            if not enabled:
                command(["systemctl", "--user", "disable", service], check=False)
            for path, content in old_files.items():
                if content is None:
                    if path.exists():
                        path.unlink()
                else:
                    atomic_write(path, content)
            command(["systemctl", "--user", "daemon-reload"], check=False)
            if enabled:
                command(["systemctl", "--user", "enable", service], check=False)
        if active and (stopped or changed):
            restored = command(["systemctl", "--user", "start", service], check=False)
            if restored.returncode:
                print("WARNING: previous service could not restart; inspect systemctl --user status " + service, file=sys.stderr)
        raise


def validate_username(value):
    if not re.fullmatch(r"[a-z0-9_.-]{3,48}", value):
        raise InstallError("App username must be 3-48 letters, digits, underscores, dots or hyphens.")


def parser():
    result = argparse.ArgumentParser(description="Install GPU Radar for your Linux SSH user; no sudo around this script.")
    result.add_argument("--username", help="GPU Radar App account (prompted for a new binding if omitted)")
    result.add_argument("--name", help="Display name, default short hostname for a new binding")
    result.add_argument("--mirror", choices=["aliyun"], help="Download the same verified agent from Aliyun instead of GitHub Releases")
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
    home = Path.home()
    if home != Path(pwd.getpwuid(os.getuid()).pw_dir):
        raise InstallError("HOME differs from your Linux account home. Run from a normal SSH login without overriding HOME or sudo.")
    refuse_legacy_installation(home)
    paths = host_paths(home, machine_id)
    package, config_dir, unit_dir = paths["package"], paths["config_dir"], paths["unit_dir"]
    service = paths["service"]
    for directory in (package, config_dir, unit_dir):
        safe_directory(home, directory)
    os.chmod(str(package), 0o700)
    os.chmod(str(config_dir), 0o700)
    installed = package / "gpu-radar-agent.pyz"
    config = config_dir / "agent.json"
    unit = paths["unit"]
    lock = package / ".install.lock"
    for path in (installed, config, unit, lock, Path(str(config) + ".lock")):
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
            # Check all destinations before obtaining a new remote device binding.
            for directory in (config_dir, unit_dir):
                probe_fd, probe_path = tempfile.mkstemp(prefix=".write-check-", dir=str(directory))
                os.close(probe_fd)
                os.unlink(probe_path)
            unit_text(machine_id)
            username = pwd.getpwuid(os.getuid()).pw_name
            systemd_preflight(username)
            if binding is None:
                enroll(artifact, config, args.username, name, machine_id)
            activate(artifact, installed, unit, config, machine_id)
            if not linger_enabled(username):
                raise InstallError("Service is running, but Linger is not enabled; reboot persistence could not be confirmed.")
        print("Installed: reporting every 5 seconds; service active and enabled; Linger=yes.")
        print("The agent continues after SSH logout and starts after reboot.")
        print("First report accepted. Allow 5 minutes of continuous samples for color classification.")
        print("Status: systemctl --user status " + service)
        print("Logs:   journalctl --user -u " + service + " -n 30 --no-pager")
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
