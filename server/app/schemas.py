from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Memory = Annotated[int, Field(ge=0, le=1_073_741_824)]
Metric = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Payload):
    username: str = Field(min_length=3, max_length=48, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value):
        return value.lower()


class Registration(Login):
    password: str = Field(min_length=10, max_length=128)


class PasswordChange(Payload):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class Enrollment(Payload):
    name: str = Field(min_length=1, max_length=80)
    hostname: str = Field(min_length=1, max_length=253)

    @field_validator("name", "hostname")
    @classmethod
    def clean_label(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("name and hostname must be nonempty printable labels")
        return value.strip()


class GPUProcess(Payload):
    pid: int = Field(ge=1, le=2_147_483_647)
    username: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=256)
    memory_used_mib: Memory | None = None


class GPU(Payload):
    uuid: str = Field(min_length=1, max_length=128)
    index: int = Field(ge=0, le=1023)
    name: str = Field(min_length=1, max_length=256)
    memory_total_mib: Memory | None = None
    memory_used_mib: Memory | None = None
    utilization_gpu_percent: Metric | None = None
    temperature_celsius: Annotated[float, Field(ge=-100, le=250, allow_inf_nan=False)] | None = None
    power_watts: Annotated[float, Field(ge=0, le=10000, allow_inf_nan=False)] | None = None
    processes_known: bool
    processes: list[GPUProcess] = Field(max_length=256)

    @model_validator(mode="after")
    def validate_memory(self):
        if self.memory_used_mib is not None and self.memory_total_mib is not None:
            if self.memory_used_mib > self.memory_total_mib:
                raise ValueError("used GPU memory cannot exceed total memory")
        return self


class Report(Payload):
    sampled_at: datetime
    hostname: str = Field(min_length=1, max_length=253)
    collection_status: Literal["ok", "error"]
    error: str | None = Field(default=None, max_length=1000)
    gpus: list[GPU] = Field(max_length=64)

    @field_validator("sampled_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("sampled_at requires an explicit timezone")
        return value

    @model_validator(mode="after")
    def validate_report(self):
        if self.collection_status == "error" and not (self.error and self.error.strip()):
            raise ValueError("collection error requires an error description")
        if self.collection_status == "ok" and self.error is not None:
            raise ValueError("successful collection must have error=null")
        if len({gpu.uuid for gpu in self.gpus}) != len(self.gpus):
            raise ValueError("GPU UUIDs must be unique within a report")
        if len({gpu.index for gpu in self.gpus}) != len(self.gpus):
            raise ValueError("GPU indices must be unique within a report")
        return self
