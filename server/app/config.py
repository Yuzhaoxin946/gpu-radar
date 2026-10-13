"""Small, explicit environment configuration shared by the API and CLI."""

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, str(default)).lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1", "yes"}


def _positive(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./gpu-radar.db"
    registration_enabled: bool = True
    session_ttl_days: int = 30
    stale_after_seconds: int = 30
    utilization_window_seconds: int = 300
    max_future_seconds: int = 30
    max_body_bytes: int = 1_048_576
    login_attempts: int = 8
    login_ip_attempts: int = 120
    login_window_seconds: int = 600

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.environ.get("DATABASE_URL", cls.database_url)
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return cls(
            database_url=url,
            registration_enabled=_bool("REGISTRATION_ENABLED", True),
            session_ttl_days=_positive("SESSION_TTL_DAYS", 30),
            stale_after_seconds=_positive("STALE_AFTER_SECONDS", 30),
        )
