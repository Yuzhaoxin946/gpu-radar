# GPU Radar MVP API contract

All paths use `/api/v1`. JSON uses snake_case. Dates are ISO-8601 UTC strings with `Z` or a UTC offset. Identifiers are opaque strings. API errors use FastAPI `detail`. Account bearer tokens and device bearer tokens are separate credentials.

## Accounts

- `POST /auth/register` body `{ "username": "researcher", "password": "..." }` creates an account and returns a login response. Account registration may be disabled by deployment configuration.
- `POST /auth/login` same body returns `{ "access_token": "...", "token_type": "bearer", "expires_at": "...", "account": { "id": "...", "username": "researcher" } }`.
- `GET /auth/me` using an account token returns `{ "id": "...", "username": "researcher" }`.
- `POST /auth/logout` revokes the current account session and returns 204.
- `POST /auth/password` using an account token, body `{ "current_password": "...", "new_password": "..." }`, verifies the current password, changes it, revokes all previous account sessions and returns a fresh login response. New passwords contain 10–128 characters. Existing device tokens continue reporting. Register and change-password controls are provided only in the macOS desktop window, not in the menu panel or Agent CLI; the HTTPS API supports those controls.

## Agent enrollment and reporting

- `POST /agents/enroll` using an account token, body `{ "name": "lab-a", "hostname": "gpu-host" }`, returns `{ "server_id": "...", "device_token": "...", "name": "lab-a" }`. A record belongs to exactly one account. Agent signs in interactively, enrolls once, then saves only its device token and server configuration, never the account password or login token. It logs out the transient account session after enrollment.
- `POST /agent/report` using a device token, body below, returns `{ "accepted": true }`.

```json
{
  "sampled_at": "2026-10-13T01:00:00Z",
  "hostname": "gpu-host",
  "collection_status": "ok",
  "error": null,
  "gpus": [{
    "uuid": "GPU-example",
    "index": 0,
    "name": "NVIDIA A100-SXM4-80GB",
    "memory_total_mib": 81920,
    "memory_used_mib": 400,
    "utilization_gpu_percent": 0,
    "temperature_celsius": 35,
    "power_watts": 50,
    "processes_known": true,
    "processes": [{ "pid": 123, "username": "alice", "name": "python", "memory_used_mib": 2048 }]
  }]
}
```

`collection_status` is `ok` or `error`. An error report has `error` text and may have an empty GPU list. Per-GPU numbers and process username/name/memory may be null if unavailable; `uuid`, `index`, `name`, `processes_known`, and `processes` are required. Unknown metrics must not be interpreted as zero. Memory is MiB, display as GiB by dividing by 1024. Report collections and process counts are bounded on the server. No complete command lines or environment variables are collected.

## Dashboard

- `GET /servers` using account token returns `{ "servers": [Server], "server_time": "...Z" }`. The macOS client uses the center clock to interpret GPU timestamps and a local elapsed-time clock to expire its own cache.
- `DELETE /servers/{server_id}` unbinds the server and invalidates its device token, returns 204. Cross-account access returns 404.
- `GET /health` returns `{ "status": "ok", "server_time": "...Z" }` without auth. Agent runtime synchronizes to this center clock and advances it using a monotonic timer, so a misconfigured Linux wall clock does not make fresh telemetry appear stale or from the future. The one-shot diagnostic `collect` command does not contact a center and retains the local timestamp.

`Server` contains:

```json
{
  "id": "opaque-id",
  "name": "lab-a",
  "hostname": "gpu-host",
  "created_at": "2026-10-13T01:00:00Z",
  "last_seen": "2026-10-13T01:00:00Z",
  "sampled_at": "2026-10-13T01:00:00Z",
  "collection_status": "ok",
  "error": null,
  "offline": false,
  "gpus": [{ "uuid": "GPU-example", "index": 0, "name": "NVIDIA A100-SXM4-80GB", "memory_total_mib": 81920, "memory_used_mib": 400, "utilization_gpu_percent": 0, "utilization_avg_5m_percent": 0.5, "utilization_window_seconds": 300, "temperature_celsius": 35, "power_watts": 50, "processes_known": true, "processes": [], "state": "idle", "idle_since": "2026-10-13T00:58:00Z" }]
}
```

Before first report `last_seen`/`sampled_at` are null, `collection_status` is `pending`, `offline` true, and `gpus` empty. Server is authoritative for classification. Default: reports every 5 seconds; stale after 30 seconds. v0.2 uses the time-weighted utilization average over the last 300 seconds, ending at the latest accepted report. Values hold until the next sample; server receipt times provide the interval lengths. A full continuous window is required. Dashboard reads alone cannot advance coverage or turn a card green.

| Wire state | Desktop label | Rule |
| --- | --- | --- |
| `idle` | 空闲 / green | Complete 5-minute average <10%, latest free memory >90% |
| `uncertain` | 被占用 / yellow | Complete average <10%, latest free memory <=90% |
| `busy` | 使用中 / red | Complete average >=10%, regardless of memory availability |
| `unknown` | 未知 / gray | Incomplete window, unknown utilization, stale/error data, or unavailable memory needed to distinguish idle/occupied |

The equality boundaries are conservative: exactly 10% average is busy; exactly 90% free memory is occupied. Process visibility is informational and no longer gates the three utilization states. `utilization_avg_5m_percent` is null until a complete valid window is available; `utilization_window_seconds` describes collected coverage, capped at 300. Per-GPU rolling history persists internally in the existing JSON snapshot but is never exposed to clients. A gap exceeding freshness, collection failure, missing utilization, or disappearance/reappearance of a GPU resets coverage. Stale timestamps, errors and offline status override classification. Preserve last known GPU rows for gray display after failures. Never let out-of-order reports replace a newer snapshot. The desktop also grays stale caches after sleep or disconnection.

MVP does not reserve resources, run remote commands or schedule jobs. GPU ordering is by current `index`; identity is `uuid`. The menu panel has exactly one row per server; mixed GPU models are combined in its model label, and squares stay in index order on a single horizontal row. Only the main desktop window offers GPU detail interactions. The macOS backend address is fixed at `https://123.56.135.166` with no address input or saved preference override.
