"""Small, private device credential store. Account credentials never go here."""

import ipaddress
import json
import os
from pathlib import Path
import stat
import tempfile
from urllib.parse import urlsplit, urlunsplit


def default_config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gpu-radar" / "agent.json"


def normalize_server(value: str, allow_insecure_http: bool = False) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Server must be an https:// URL (localhost may use http://).")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Server URL must not contain credentials, query parameters or fragments.")
    # Accessing port validates malformed ports before any password is sent.
    _ = parsed.port
    host = parsed.hostname.lower()
    local = host == "localhost"
    try:
        local = local or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if parsed.scheme == "http" and not local and not allow_insecure_http:
        raise ValueError("Non-local HTTP exposes credentials. Use HTTPS, or explicitly pass --allow-insecure-http for a trusted test network.")
    path = parsed.path.rstrip("/")
    if path.endswith("/api/v1"):
        path = path[:-7]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def write_config(path: Path, config: dict) -> None:
    path = path.expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Refusing to replace a symlink configuration file.")
    fd, temporary = tempfile.mkstemp(prefix=".agent-", suffix=".json", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_config(path: Path) -> dict:
    path = path.expanduser()
    # O_NOFOLLOW closes the race between checking a link and opening its target.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Config must be a regular file.")
        if metadata.st_mode & 0o077:
            raise ValueError("Config contains a device credential; run chmod 600 on it before continuing.")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise ValueError("Config must be owned by the account running this agent.")
        config = json.load(handle)
    if not isinstance(config, dict) or not all(isinstance(config.get(key), str) and config[key] for key in ("server", "server_id", "device_token", "name")):
        raise ValueError("Invalid configuration; log in again.")
    config["server"] = normalize_server(config["server"], config.get("allow_insecure_http") is True)
    return config
