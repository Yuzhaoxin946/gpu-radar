"""Five-minute time-weighted GPU utilization, evaluated only on accepted reports."""

from datetime import datetime
from math import fsum

from .config import Settings
from .security import timestamp, utc

HISTORY_KEY = "_utilization_history"
MAX_HISTORY_POINTS = 1024


def is_fresh(sampled_at: datetime | None, last_seen: datetime | None, now: datetime, settings: Settings) -> bool:
    if sampled_at is None or last_seen is None:
        return False
    return (
        (now - utc(sampled_at)).total_seconds() <= settings.stale_after_seconds
        and (now - utc(last_seen)).total_seconds() <= settings.stale_after_seconds
        and (utc(sampled_at) - now).total_seconds() <= settings.max_future_seconds
    )


def next_history(previous: list, now: float, utilization: float, window: int) -> tuple[list, float | None, float]:
    """Hold each previous value until the next receipt; keep an anchor before the left boundary."""
    history = [list(point) for point in previous]
    if history and now < history[-1][0]:
        history = []
    if history and now == history[-1][0]:
        history[-1] = [now, utilization]
    else:
        history.append([now, utilization])
    cutoff = now - window
    anchor = 0
    for index, point in enumerate(history):
        if point[0] <= cutoff:
            anchor = index
        else:
            break
    history = history[anchor:][-MAX_HISTORY_POINTS:]
    coverage = max(0.0, min(float(window), now - history[0][0]))
    if coverage < window:
        return history, None, coverage
    integral = fsum(
        (right[0] - max(left[0], cutoff)) * left[1]
        for left, right in zip(history, history[1:])
        if right[0] > cutoff
    )
    return history, integral / window, coverage


def base_state(gpu: dict) -> str:
    average = gpu.get("utilization_avg_5m_percent")
    if average is None:
        return "unknown"
    # A known busy utilization window is sufficient even if memory cannot be read.
    if average >= 10:
        return "busy"
    total, used = gpu.get("memory_total_mib"), gpu.get("memory_used_mib")
    if total is None or used is None or total <= 0:
        return "unknown"
    # Integer arithmetic preserves the exact 90% free-memory boundary.
    return "idle" if (total - used) * 10 > total * 9 else "uncertain"


def reset_row(gpu: dict) -> dict:
    return {**gpu, HISTORY_KEY: [], "utilization_avg_5m_percent": None,
            "utilization_window_seconds": 0.0, "idle_since": None}


def next_gpu_snapshot(server, report, now: datetime, settings: Settings) -> list[dict]:
    if report.collection_status == "error":
        rows = server.gpus or [gpu.model_dump(mode="json") for gpu in report.gpus]
        return [reset_row(gpu) for gpu in rows]

    previous = {gpu["uuid"]: gpu for gpu in server.gpus}
    current_fresh = is_fresh(report.sampled_at, now, now, settings)
    continuous = (
        server.collection_status == "ok"
        and is_fresh(server.sampled_at, server.last_seen, now, settings)
        and current_fresh
        and server.sampled_at is not None
        and (utc(report.sampled_at) - utc(server.sampled_at)).total_seconds() <= settings.stale_after_seconds
    )
    rows = []
    for gpu in report.gpus:
        row = reset_row(gpu.model_dump(mode="json"))
        prior = previous.get(gpu.uuid, {})
        utilization = gpu.utilization_gpu_percent
        if utilization is not None and current_fresh:
            history = prior.get(HISTORY_KEY, []) if continuous else []
            points, average, coverage = next_history(history, now.timestamp(), utilization, settings.utilization_window_seconds)
            row.update({HISTORY_KEY: points, "utilization_avg_5m_percent": average,
                        "utilization_window_seconds": coverage})
        if base_state(row) == "idle":
            row["idle_since"] = (prior.get("idle_since") if continuous and base_state(prior) == "idle" else None) or timestamp(now)
        rows.append(row)
    return sorted(rows, key=lambda row: row["index"])


def server_view(server, now: datetime, settings: Settings) -> dict:
    offline = not is_fresh(server.sampled_at, server.last_seen, now, settings)
    invalid = offline or server.collection_status != "ok"
    rows = []
    for gpu in sorted(server.gpus, key=lambda row: row["index"]):
        row = {key: value for key, value in gpu.items() if not key.startswith("_")}
        row.setdefault("utilization_avg_5m_percent", None)
        row.setdefault("utilization_window_seconds", 0.0)
        row.setdefault("idle_since", None)
        row["state"] = "unknown" if invalid else base_state(row)
        if row["state"] != "idle":
            row["idle_since"] = None
        if invalid:
            row["utilization_avg_5m_percent"] = None
            row["utilization_window_seconds"] = 0.0
        rows.append(row)
    return {
        "id": server.id, "name": server.name, "hostname": server.hostname,
        "created_at": timestamp(server.created_at), "last_seen": timestamp(server.last_seen),
        "sampled_at": timestamp(server.sampled_at), "collection_status": server.collection_status,
        "error": server.error, "offline": offline, "gpus": rows,
    }
