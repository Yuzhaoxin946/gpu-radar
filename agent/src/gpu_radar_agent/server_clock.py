"""Backend-relative sample timestamps, independent of the GPU host's clock."""

from datetime import datetime, timedelta, timezone
import re
import time

from .api import APIError


def parse_server_time(value):
    """Parse aware ISO-8601 with Z or an offset using only Python 3.6 APIs."""
    if not isinstance(value, str):
        raise ValueError("Server time is missing.")
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:?\d{2})",
        value,
    )
    if not match:
        raise ValueError("Server time is not a timezone-aware ISO timestamp.")
    base, fraction, offset = match.groups()
    zone = timezone.utc
    if offset != "Z":
        digits = offset[1:].replace(":", "")
        hours, minutes = int(digits[:2]), int(digits[2:])
        if hours > 23 or minutes > 59:
            raise ValueError("Server time has an invalid UTC offset.")
        delta = timedelta(hours=hours, minutes=minutes)
        zone = timezone(delta if offset[0] == "+" else -delta)
    parsed = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
    return parsed.replace(microsecond=int((fraction or "").ljust(6, "0")), tzinfo=zone)


class ServerClock:
    def __init__(self, sync_interval=300, monotonic=None):
        self.sync_interval = sync_interval
        self.monotonic = monotonic or time.monotonic
        self.anchor_epoch = None
        self.anchor_monotonic = None

    def ensure_synced(self, client):
        now = self.monotonic()
        if self.anchor_monotonic is not None and 0 <= now - self.anchor_monotonic < self.sync_interval:
            return
        # A failed scheduled refresh must not silently keep an expired anchor.
        self.anchor_epoch = None
        self.anchor_monotonic = None
        started = self.monotonic()
        response = client.request("GET", "/health")
        finished = self.monotonic()
        if finished < started:
            raise APIError(None, "Monotonic clock moved backward during synchronization.")
        try:
            if not isinstance(response, dict) or response.get("status") != "ok":
                raise ValueError("Health response is not ready.")
            server_epoch = parse_server_time(response.get("server_time")).timestamp()
        except (ValueError, OverflowError):
            raise APIError(None, "Health response lacks a valid server_time; update or check the backend.") from None
        # Assume roughly symmetric latency. At receipt, server_time has advanced
        # by about half the round-trip; subsequent elapsed time is monotonic.
        self.anchor_epoch = server_epoch + (finished - started) / 2.0
        self.anchor_monotonic = finished

    def timestamp(self):
        if self.anchor_epoch is None or self.anchor_monotonic is None:
            raise APIError(None, "Backend clock has not been synchronized.")
        elapsed = self.monotonic() - self.anchor_monotonic
        if elapsed < 0 or elapsed >= self.sync_interval:
            raise APIError(None, "Backend clock synchronization expired before sampling.")
        return datetime.fromtimestamp(self.anchor_epoch + elapsed, timezone.utc).isoformat().replace("+00:00", "Z")
