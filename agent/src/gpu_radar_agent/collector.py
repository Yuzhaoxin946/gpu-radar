"""Read-only NVIDIA collection; missing information is never reported as zero.

The CLI runs this module in a child process, bounding even stuck NVML calls.
"""

import csv
from datetime import datetime, timezone
import importlib
import io
import json
import math
from pathlib import Path
import pwd
import socket
import subprocess
import sys
from typing import List, Optional, Tuple

MIB = 1024 * 1024
MAX_GPUS = 64
MAX_PROCESSES = 256


class CollectionError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def number(value, integer: bool = False):
    """Normalize NVIDIA's N/A, NVML sentinel and invalid numeric values."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).strip())
    except (ValueError, TypeError):
        return None
    if not math.isfinite(result) or result < 0 or result >= 2**63:
        return None
    if integer and result != int(result):
        return None
    return int(result) if integer else result


def process_metadata(pid: int) -> Tuple[Optional[str], Optional[str]]:
    """Only ownership and /proc comm; never read cmdline, environ or argv."""
    root = Path("/proc") / str(pid)
    try:
        username = pwd.getpwuid(root.stat().st_uid).pw_name
    except (OSError, KeyError):
        username = None
    try:
        name = (root / "comm").read_text(encoding="utf-8", errors="replace").strip()[:200] or None
    except OSError:
        name = None
    return username, name


def csv_rows(text: str, fields: int) -> List[List[str]]:
    result = []
    try:
        for row in csv.reader(io.StringIO(text), skipinitialspace=True, strict=True):
            if not row or all(not column.strip() for column in row):
                continue
            if len(row) != fields:
                raise CollectionError("Unexpected nvidia-smi CSV shape; cannot trust this sample.")
            result.append([column.strip() for column in row])
    except csv.Error:
        raise CollectionError("Malformed nvidia-smi CSV.") from None
    return result


def parse_gpu_csv(text: str, includes_mig: bool = True) -> List[dict]:
    rows = csv_rows(text, 9 if includes_mig else 8)
    if not rows:
        raise CollectionError("No NVIDIA GPUs found.")
    if len(rows) > MAX_GPUS:
        raise CollectionError("GPU count exceeds the supported limit.")
    gpus = []
    seen = set()
    seen_indices = set()
    for row in rows:
        uuid, index, name, total, used, utilization, temperature, power = row[:8]
        index = number(index, integer=True)
        if not uuid.startswith("GPU-") or index is None or not name or uuid in seen or index in seen_indices:
            raise CollectionError("GPU identity is unavailable, duplicated or unsupported (including MIG).")
        seen.add(uuid)
        seen_indices.add(index)
        mig_value = row[8].lower() if includes_mig else "unknown"
        # N/A denotes MIG unsupported on non-MIG devices. Unknown states stay gray.
        mig_safe = mig_value in {"disabled", "0", "n/a", "[n/a]", "[not supported]", "not supported"}
        gpus.append({
            "uuid": uuid, "index": index, "name": name,
            "memory_total_mib": number(total, True), "memory_used_mib": number(used, True),
            "utilization_gpu_percent": number(utilization), "temperature_celsius": number(temperature),
            "power_watts": number(power), "processes_known": mig_safe, "processes": [],
        })
    return sorted(gpus, key=lambda gpu: gpu["index"])


def apply_process_csv(gpus: List[dict], text: str) -> None:
    by_uuid = {gpu["uuid"]: gpu for gpu in gpus}
    rows = csv_rows(text, 3)
    if len(rows) > MAX_GPUS * MAX_PROCESSES:
        raise CollectionError("Process count exceeds the supported limit.")
    for uuid, pid_value, memory in rows:
        pid = number(pid_value, True)
        if uuid not in by_uuid or pid is None or pid <= 0:
            raise CollectionError("GPU process identity is unavailable or unsupported (including MIG).")
        username, name = process_metadata(pid)
        gpu = by_uuid[uuid]
        if len(gpu["processes"]) >= MAX_PROCESSES:
            raise CollectionError("Per-GPU process count exceeds the supported limit.")
        gpu["processes"].append({"pid": pid, "username": username, "name": name, "memory_used_mib": number(memory, True)})


def smi_query(query: str, timeout: float = 3) -> str:
    try:
        result = subprocess.run(["nvidia-smi", query, "--format=csv,noheader,nounits"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise CollectionError("nvidia-smi is not installed or is not on PATH.") from None
    except subprocess.TimeoutExpired:
        raise CollectionError("nvidia-smi timed out.") from None
    if result.returncode:
        raise CollectionError(f"nvidia-smi query failed with exit code {result.returncode}.")
    if len(result.stdout) > 2 * 1024 * 1024:
        raise CollectionError("nvidia-smi output exceeds the supported size.")
    return result.stdout


def collect_smi() -> List[dict]:
    fields = "uuid,index,name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw"
    try:
        gpus = parse_gpu_csv(smi_query("--query-gpu=" + fields + ",mig.mode.current"))
    except CollectionError:
        # Older drivers may lack mig.mode.current; still display metrics, but
        # do not assert known process visibility until MIG status is available.
        gpus = parse_gpu_csv(smi_query("--query-gpu=" + fields), includes_mig=False)
    try:
        apply_process_csv(gpus, smi_query("--query-compute-apps=gpu_uuid,pid,used_gpu_memory"))
    except CollectionError:
        for gpu in gpus:
            gpu["processes_known"] = False
            gpu["processes"] = []
    return gpus


def decoded(value) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def collect_nvml() -> List[dict]:
    try:
        nv = importlib.import_module("pynvml")
        nv.nvmlInit()
    except Exception as error:
        raise CollectionError(f"NVML unavailable ({type(error).__name__}).") from None
    try:
        count = nv.nvmlDeviceGetCount()
        if not 0 < count <= MAX_GPUS:
            raise CollectionError("No supported NVIDIA GPU devices found.")
        gpus = []
        for index in range(count):
            handle = nv.nvmlDeviceGetHandleByIndex(index)
            uuid = decoded(nv.nvmlDeviceGetUUID(handle))
            if not uuid.startswith("GPU-"):
                raise CollectionError("MIG instance enumeration is not supported in this release.")
            gpu = {"uuid": uuid, "index": index, "name": decoded(nv.nvmlDeviceGetName(handle)),
                   "memory_total_mib": None, "memory_used_mib": None, "utilization_gpu_percent": None,
                   "temperature_celsius": None, "power_watts": None, "processes_known": True, "processes": []}
            try:
                memory = nv.nvmlDeviceGetMemoryInfo(handle)
                gpu["memory_total_mib"] = int(memory.total // MIB) if number(memory.total) is not None else None
                gpu["memory_used_mib"] = int(memory.used // MIB) if number(memory.used) is not None else None
            except nv.NVMLError:
                pass
            for key, call in (
                ("utilization_gpu_percent", lambda: nv.nvmlDeviceGetUtilizationRates(handle).gpu),
                ("temperature_celsius", lambda: nv.nvmlDeviceGetTemperature(handle, nv.NVML_TEMPERATURE_GPU)),
                ("power_watts", lambda: nv.nvmlDeviceGetPowerUsage(handle) / 1000),
            ):
                try:
                    gpu[key] = number(call())
                except nv.NVMLError:
                    pass
            try:
                current_mig, _ = nv.nvmlDeviceGetMigMode(handle)
                if current_mig != 0:
                    gpu["processes_known"] = False
            except nv.NVMLError_NotSupported:
                pass  # This GPU cannot use MIG.
            except (nv.NVMLError, AttributeError):
                gpu["processes_known"] = False
            try:
                processes = nv.nvmlDeviceGetComputeRunningProcesses(handle)
                if len(processes) > MAX_PROCESSES:
                    raise CollectionError("Per-GPU process count exceeds the supported limit.")
                for process in processes:
                    username, name = process_metadata(process.pid)
                    used = number(process.usedGpuMemory)
                    gpu["processes"].append({"pid": process.pid, "username": username, "name": name,
                                             "memory_used_mib": int(used // MIB) if used is not None else None})
            except nv.NVMLError:
                gpu["processes_known"] = False
                gpu["processes"] = []
            gpus.append(gpu)
        return gpus
    except CollectionError:
        raise
    except Exception as error:
        raise CollectionError(f"NVML collection failed ({type(error).__name__}).") from None
    finally:
        try:
            nv.nvmlShutdown()
        except Exception:
            pass


def collect() -> dict:
    try:
        try:
            gpus = collect_nvml()
        except CollectionError:
            gpus = collect_smi()
        return {"sampled_at": utc_now(), "hostname": socket.gethostname(), "collection_status": "ok", "error": None, "gpus": gpus}
    except CollectionError as error:
        return error_report(str(error))


def error_report(error: str) -> dict:
    return {"sampled_at": utc_now(), "hostname": socket.gethostname(), "collection_status": "error", "error": error[:500], "gpus": []}


def simulated_report() -> dict:
    """Explicit demo only: fixed identities, continuously fresh sample times."""
    gpus = []
    for index, (model, total, used, util, processes_known) in enumerate([
        ("NVIDIA A100-SXM4-80GB", 81920, 128, 0, True),
        ("NVIDIA A100-SXM4-80GB", 81920, 70000, 96, True),
        ("NVIDIA A100-SXM4-80GB", 81920, 18000, 1, True),
        ("NVIDIA GeForce RTX 4090", 24576, None, None, False),
    ]):
        processes = [] if index in {0, 3} else [{"pid": 9000 + index, "username": "demo-user", "name": "demo-training", "memory_used_mib": used}]
        gpus.append({"uuid": f"GPU-SIMULATION-{index}", "index": index, "name": "[SIMULATION] " + model,
                     "memory_total_mib": total, "memory_used_mib": used, "utilization_gpu_percent": util,
                     "temperature_celsius": 35 + index * 8, "power_watts": 50 + index * 60,
                     "processes_known": processes_known, "processes": processes})
    return {"sampled_at": utc_now(), "hostname": "SIMULATION:" + socket.gethostname(), "collection_status": "ok", "error": None, "gpus": gpus}


def collect_bounded(timeout: float = 8) -> dict:
    # __file__ lives under either src/, site-packages/, or the .pyz archive.
    # Explicitly carry its import root into the worker, so zipapps need no
    # installed package and use the exact same collector as their parent.
    package_root = str(Path(__file__).parent.parent)
    worker = (
        "import json, sys; sys.path.insert(0, sys.argv[1]); "
        "from gpu_radar_agent.collector import collect; "
        "print(json.dumps(collect(), allow_nan=False))"
    )
    try:
        result = subprocess.run([sys.executable, "-c", worker, package_root], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return error_report("GPU collection exceeded its time limit.")
    except OSError:
        return error_report("Unable to start the GPU collection worker.")
    if result.returncode != 0:
        return error_report("GPU collection worker failed.")
    try:
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or report.get("collection_status") not in {"ok", "error"}:
            raise ValueError()
        return report
    except ValueError:
        return error_report("GPU collection worker returned an invalid report.")


if __name__ == "__main__":
    print(json.dumps(collect(), allow_nan=False))
