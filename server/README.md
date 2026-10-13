# GPU Radar service

Python 3.11+ / FastAPI / SQLAlchemy. SQLite is suitable for a local trial; set `DATABASE_URL` to use PostgreSQL in the cloud. The API is specified in [`../docs/api-contract.md`](../docs/api-contract.md).

## Local start

Run these commands from `server/`:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The registration endpoint is enabled by default, but every new account requires a valid invitation, including local development. An installation with no invitations cannot accept self-service registrations. Interactive API docs are at `http://127.0.0.1:8000/docs`; `GET /api/v1/health` returns `status: "ok"` and `server_time`, the current UTC server time as an ISO-8601 string ending in `Z`. Agents can use this time to calibrate report timestamps without changing their host clock. The report endpoint still rejects samples more than 30 seconds in the future. SQLite creates `gpu-radar.db` in the working directory and preserves account/device state and the latest snapshot across restarts.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./gpu-radar.db` | SQLite URL or `postgresql+psycopg://user:password@db:5432/gpu_radar` |
| `REGISTRATION_ENABLED` | `true` | Allow invitation-only registration; `false` disables all self-service registration, including with a valid invitation |
| `SESSION_TTL_DAYS` | `30` | Account login expiry; device credentials are independently revocable |
| `STALE_AFTER_SECONDS` | `30` | Data older than this is gray, including stale agent timestamps |

The utilization window is fixed at 300 seconds in production (`Settings.utilization_window_seconds`). The old `IDLE_CONFIRM_SECONDS` setting is no longer used.

Use one Uvicorn worker: the basic login limiter is local to the process. Terminate HTTPS at a trusted reverse proxy for deployment; configure forwarded headers only for that proxy. Do not expose a plaintext password login on the public internet. PostgreSQL credentials belong in the deployment environment, not source control.

## Invitation-only registration

New accounts require an invitation issued privately by the operator. Each invitation has a total account-creation limit, and only a successful account creation consumes one use. Invalid requests and duplicate usernames do not consume uses. The use counter and new account are committed atomically, preventing concurrent registration from exceeding the limit; counters persist in the database across restarts. Existing login sessions, password changes, and Agent enrollment/reporting are unaffected. Invitations are not stored in client builds or public source.

To create an invitation allowing five new accounts, run this from `server/` with the same `DATABASE_URL` as the API:

```sh
python -m app.manage create-invite --max-uses 5
```

The code is prompted without echo; send it privately to intended users. Invitation codes use 8–128 ASCII letters, digits, `_`, or `-`. They are case-sensitive after trimming surrounding whitespace, and the database stores only their hashes. Repeating this command with the same code and limit preserves the existing use count; a different limit is refused, so rerunning deployment does not replenish an invitation. `--code-stdin` is available for trusted automation without putting the code in command arguments. Never commit an actual invitation or include it in public release notes. Codes do not expire automatically in this version.

An administrator can also create the first account directly without an invitation, using the same `DATABASE_URL` as the API:

```sh
python -m app.manage create-user researcher
```

Passwords must contain 10–128 characters and are prompted twice without echo. Usernames use 3–48 ASCII letters, digits, `.`, `_`, or `-`, and normalize to lowercase. Passwords use salted scrypt (`N=32768, r=8, p=3`). Random account and device tokens are stored only as SHA-256 digests. Device tokens cannot access the dashboard, and account tokens cannot upload telemetry. Deleting a server both removes its snapshot and revokes its device credential.

## Password changes

An authenticated account can `POST /api/v1/auth/password` with `{"current_password": "…", "new_password": "…"}`. The new password requires 10–128 characters. Success returns the same login response shape as `/auth/login`, containing a replacement account session. The client must replace its stored token. All older sessions for that account are revoked atomically; its device credentials and other accounts' sessions remain valid.

Password verification shares login's source-IP and account failure budgets. Wrong current password returns `403` (`Current password is incorrect`), expired/revoked session returns `401`, concurrent credential rotation returns `409` (`Account credentials changed; sign in again`), invalid fields return `422`, and rate limiting returns `429`. Conditional password-hash updates serialize both concurrent rotations and logins that had already verified an old password, including on SQLite.

## State semantics

- **Green (`idle`, 空闲)**: latest free memory is strictly greater than 90%, and the complete trailing five-minute average GPU utilization is below 10%.
- **Yellow (`uncertain`, 已占用)**: latest free memory is at most 90%, and the complete trailing five-minute average utilization is below 10%. The wire value stays `uncertain` for compatibility.
- **Red (`busy`, 使用中)**: complete five-minute average utilization is at least 10%, including exactly 10%. High utilization is sufficient even when memory telemetry is unavailable.
- **Gray (`unknown`)**: less than 300 seconds of continuous utilization coverage, unknown current utilization, collection failure, stale data, or unknown memory when average utilization is below 10%. Process visibility does not affect classification. A collection failure preserves previous GPU rows for gray display.

The average is time-weighted using service receipt time: each previous utilization value is held until the next accepted report. The window ends at the latest accepted receipt, and dashboard reads never advance coverage. Each GPU returns `utilization_avg_5m_percent` (number or null) and `utilization_window_seconds` (0–300). A null utilization resets the window; a missing memory metric leaves valid utilization history intact. `idle_since` is the time the GPU first qualified as green under these rules, or null.

GPU identity uses UUID, while display order uses the current device index. Reports are limited to 64 GPUs, 256 processes per GPU and 1 MiB total JSON. Timestamps must have a timezone; more than 30 seconds in the future is rejected. Duplicate/out-of-order samples do not update the snapshot, renew freshness, or advance the utilization window. Receipt/sample gaps longer than 30 seconds and collection errors reset coverage.

A bounded history is persisted inside the existing GPU JSON snapshot, keeping one sample before the left window boundary for correct integration. It survives service restarts and needs no schema migration. Old snapshots without history warm up from zero coverage. Internal history is never exposed by the API. There are at most 1024 points per GPU; unusually high sample rates that exceed that cap conservatively shorten coverage and show gray rather than inventing a complete window. The service does not provide historical charts or reserve devices.

`GET /api/v1/servers` includes a top-level UTC `server_time` alongside `servers`. It is the same instant used to classify all records in that response, allowing clients to show data age relative to the central service even when their local clock differs.

The basic login limiter has two independent budgets: 120 total login/password-change requests per source IP in ten minutes, and eight failed or in-flight attempts per username in ten minutes across all IPs. Successful verification clears only that account's completed failures, and never resets the IP budget. Concurrent password checks reserve an account slot before verification. Registration permits eight attempts per source IP in ten minutes. A reverse proxy can provide a broader deployment rate limit. The app creates the initial schema on startup; future schema changes will require migrations before upgrading an existing database.

## Tests

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests exercise account isolation, token scope/expiry/revocation, password rotation and concurrent authentication, persistent hash storage, weighted irregular sampling and exact boundaries, history persistence/caps, stale/error handling, GPU identity, unknown telemetry, replay/out-of-order reports, limits, throttling, disabled registration, invitation limits and concurrent redemption, failed-registration rollback, persistence across restarts, and administrator bootstrap. PostgreSQL schema compilation is checked without requiring a live database; a live PostgreSQL deployment is a separate integration check.
