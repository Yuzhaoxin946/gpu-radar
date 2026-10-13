import base64
import hashlib
import hmac
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes even for timezone=True columns.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def timestamp(value: datetime | None) -> str | None:
    return utc(value).isoformat().replace("+00:00", "Z") if value else None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3, dklen=32, maxmem=128 * 1024 * 1024)
    return "scrypt$32768$8$3$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(key).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = stored.split("$")
        if algorithm != "scrypt":
            return False
        key = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32, maxmem=128 * 1024 * 1024)
        return hmac.compare_digest(key, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invitation_hash(code: str) -> str:
    return hashlib.sha256(("gpu-radar-invite-v1:" + code.strip()).encode()).hexdigest()


def issue_token(prefix: str) -> tuple[str, str]:
    token = prefix + secrets.token_urlsafe(32)
    return token, token_hash(token)


class LoginLimiter:
    """Bounded single-worker limiter. Reverse proxy may add an IP rate limit."""
    def __init__(self, attempts: int, window_seconds: int):
        self.attempts = attempts
        self.window = window_seconds
        self.entries: OrderedDict[str, list[float]] = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key: str, now: float) -> bool:
        with self.lock:
            events = [t for t in self.entries.pop(key, []) if now - t < self.window]
            allowed = len(events) < self.attempts
            if allowed:
                events.append(now)
            self.entries[key] = events
            while len(self.entries) > 4096:
                self.entries.popitem(last=False)
            return allowed

@dataclass
class _AccountAttempts:
    failures: list[float] = field(default_factory=list)
    inflight: int = 0


class LoginFailureLimiter:
    """Account failure budget, reserving in-flight attempts before password verification."""

    def __init__(self, attempts: int, window_seconds: int):
        self.attempts, self.window = attempts, window_seconds
        self.entries: OrderedDict[str, _AccountAttempts] = OrderedDict()
        self.lock = threading.Lock()

    def begin(self, username: str, now: float) -> bool:
        with self.lock:
            bucket = self.entries.pop(username, None)
            if bucket is None:
                if len(self.entries) >= 4096:
                    # Never evict an attempt while its password verification is running.
                    removable = next((key for key, value in self.entries.items() if not value.inflight), None)
                    if removable is None:
                        return False
                    self.entries.pop(removable)
                bucket = _AccountAttempts()
            bucket.failures = [t for t in bucket.failures if now - t < self.window]
            self.entries[username] = bucket
            if len(bucket.failures) + bucket.inflight >= self.attempts:
                return False
            bucket.inflight += 1
            return True

    def finish(self, username: str, now: float, success: bool):
        with self.lock:
            bucket = self.entries[username]
            bucket.inflight -= 1
            if success:
                # Clear only this account's completed failures, retaining concurrent attempts.
                bucket.failures.clear()
            else:
                bucket.failures.append(now)
