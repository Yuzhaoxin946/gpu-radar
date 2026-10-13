# GPU Radar

[简体中文](README.md) | **English**

Check GPU availability across multiple Linux servers from your Mac menu bar.

GPU Radar consists of a Linux collector, a central API, and a native macOS app. Each server gets one menu row: its name, GPU model, and one colored square per GPU. The squares display status without click actions; the desktop window shows utilization, memory, and process details.

Each app account manages its own servers. An Agent signs in to the app account during installation, then uses an independent device credential for subsequent reports. It does not store the account password.

[Download releases](https://github.com/Yuzhaoxin946/gpu-radar/releases/latest) · [Complete Linux installation and maintenance guide](agent/README.en.md) · [Validation scope (Chinese)](docs/validation.md)

## Where to start

1. **Create an account on your Mac**: open the desktop window, register, and sign in. If you do not have the app yet, follow [Build the macOS app](#build-the-macos-app) below.
2. **Install an Agent on each GPU server**: SSH into the server normally, then run the installation command below in its interactive terminal. Use the same app account to add servers to one dashboard.
3. **Check your Mac dashboard**: after installation, reports arrive every 5 seconds. New GPUs appear gray until 5 minutes of continuous samples have accumulated.

## Install the Linux Agent

First register in the Mac app. Then SSH into the GPU server as your usual Linux user and run:

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --username your_account
```

Replace `your_account` with your **app username**. Enter your app password when prompted; input is hidden, and the password does not belong in the command line. The server name defaults to its hostname. Append `--name lab-a` to choose a display name for a new installation.

Requirements: Linux, Bash, curl, Python 3.6+, working `nvidia-smi`, user-level systemd, and outbound HTTPS to GitHub and the backend. **Do not put `sudo` before the installation command.** If the installer separately prompts for sudo, it needs Linux administrative authentication, which is separate from your app password. The installer:

- Downloads and verifies a standalone Agent, with no pip, Docker, or additional Python packages.
- Checks real GPU collection and HTTPS connectivity before signing in and enrolling the server.
- Configures a user-level systemd service to report every 5 seconds.
- Checks linger and startup at boot; it requests administrative help when needed and reports failure when persistence cannot be confirmed.
- Stores separate configurations and device credentials per host, supporting shared NFS home directories.
- Preserves this host's binding on repeated installation without duplicate enrollment, and restores the previous installation if an update fails.

Once the installer confirms the first report was accepted, the service is active/enabled, and Linger=yes, collection continues after SSH logout and is configured to start at boot. The first connection needs 5 continuous minutes of samples before showing green, yellow, or red. No inbound ports need to be opened on GPU servers.

To run a check without signing in or installing background services:

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --check
```

`--check` verifies the download, real GPU collection, and HTTPS. It does not test systemd or linger permissions; those are checked during full installation.

If GitHub is unreachable, explicitly use the Aliyun mirror:

```bash
curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --username your_account
```

Both sources provide the same release and verify the Agent's SHA-256 checksum. Keep `--mirror aliyun` so the Agent package also downloads from the mirror. The equivalent mirror check is:

```bash
curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --check
```

The released app and installer connect to the fixed backend at `https://123.56.135.166`. Use your own app account; Linux usernames can differ between servers.

Initial enrollment requires an interactive SSH terminal. Some jump servers do not forward a TTY for remote command execution. Log in to the GPU host normally, then paste the command. In Bash automation, run `set -o pipefail` first to detect a failed download in the pipeline.

## Everyday maintenance

These commands apply to the current installer. On the target GPU server, use the same Linux user who installed it to inspect service status, recent logs, and linger:

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
unit="gpu-radar-agent-$(cat /etc/machine-id).service"
systemctl --user status "$unit"
journalctl --user -u "$unit" -n 30 --no-pager
loginctl show-user "$(id -un)" -p Linger
```

To restart collection, use the variable above and run `systemctl --user restart "$unit"`.

To update or retry, rerun the original installation command. It keeps this host's existing binding without creating another server record. `--username` and `--name` do not change an existing account binding or rename a server; a mismatch stops installation.

Hosts sharing an NFS home directory each need a valid, unique `/etc/machine-id`. The program, configuration, lock, and service are isolated by host. Existing manual installations using `gpu-radar-agent.service` remain unchanged. If the installer detects a legacy flat configuration or service, it stops; the owning host must be verified before migration.

See the [Linux Agent guide](agent/README.en.md) for stopping collection, uninstalling, revoking device credentials, exact paths, and manual installation.

## Common questions

| Symptom | What to do |
| --- | --- |
| GitHub download times out | Use the Aliyun mirror command, including `--mirror aliyun` |
| An interactive terminal is required | Log in to the target GPU host through a normal SSH shell, then paste the command, especially when using a jump server |
| Linger or the user service manager is unavailable | Follow the installer's administrator guidance, reconnect through SSH, and retry; startup at boot is not confirmed until installation succeeds |
| `Unsafe directory` | Verify the owner and permissions of the exact directory in the error. Fix only a directory you have confirmed you manage; do not recursively change your entire home directory's permissions. See the Linux guide |
| A legacy configuration is detected | Preserve the existing configuration and service until the owning host is verified. Do not copy device credentials to other servers or delete the configuration just to bypass the check |
| GPUs stay gray | Allow 5 continuous minutes initially. If they remain gray, inspect collection logs, the network, and recent reports |
| Must Agents sign in again after an app password change? | No. Devices keep their independent credentials; previous app login sessions are revoked |

## Build the macOS app

Requires macOS 13+, full Xcode, and Swift 5.9+. Run on your **Mac**:

```bash
git clone https://github.com/Yuzhaoxin946/gpu-radar.git
cd gpu-radar
GPU_RADAR_BUILD_WORK="$PWD/.build/macos-build" ./macos/build-app.sh
open "macos/build/GPU Radar.app"
```

If you already cloned the repository, run the last two commands from its root. The output is `macos/build/GPU Radar.app`; drag it into Applications for future use.

The app provides registration, login, password changes, and demo mode. Changing a password revokes old app login sessions while enrolled GPU servers keep reporting. The menu bar provides an overview, and the desktop window shows server and GPU details.

The build script uses ad hoc signing and builds for your current Mac architecture. Developer ID signing and Apple notarization are not provided. The current GitHub Release assets contain the Linux installer and Agent, not a downloadable Mac app installer.

## Status colors

| Color | Status | Rule |
| --- | --- | --- |
| Green | Idle | Average utilization over the last 5 minutes <10%, and current free GPU memory >90% |
| Yellow | Occupied | Average utilization over the last 5 minutes <10%, and current free GPU memory ≤90% |
| Red | In use | Average utilization over the last 5 minutes ≥10% |
| Gray | Insufficient or stale data | Less than 5 minutes of samples, unavailable metrics, collection failure, or no update for more than 30 seconds |

The average is weighted by actual sample receipt intervals on the backend and stored in the database. Gaps or invalid utilization restart the sampling window. Unavailable metrics are not treated as zero. Process information is shown for context only.

Green is a monitoring result, not a GPU reservation. GPU Radar does not launch training jobs, seize GPUs, terminate processes, or schedule jobs.

## Repository structure

| Directory | Contents |
| --- | --- |
| `agent/` | Python 3.6+ collection code, installer, standalone package builder, systemd templates |
| `server/` | FastAPI, account and device authentication, database, status classification |
| `macos/` | SwiftUI desktop app and menu bar interface |
| `deploy/` | Native systemd / Nginx and optional Docker Compose configuration |
| `scripts/` | Local development, tests, and end-to-end smoke checks |
| `docs/` | API contract, deployment instructions, validation scope |

## Local development and tests

Backend development requires Python 3.11+. From the repository root:

```bash
./scripts/setup-local.sh
./scripts/start-local.sh
```

The local API listens on `http://127.0.0.1:8000`, serves API documentation at `/docs`, and stores its database at `.local/radar.db`. The released Mac app uses the fixed cloud endpoint; the Agent CLI can be used with the local API.

```bash
.venv/bin/gpu-radar-agent --config .local/demo-agent.json login \
  --server http://127.0.0.1:8000 --name demo-lab --username researcher
.venv/bin/gpu-radar-agent --config .local/demo-agent.json run --simulate
```

Create a test account through the local API first. `--simulate` explicitly labels simulated data; real collection failures never trigger an automatic simulation fallback.

```bash
./scripts/test.sh
.venv/bin/python scripts/smoke.py
cd macos && swift test
```

More details: [API contract (English)](docs/api-contract.md) · [Deployment guide (Chinese)](docs/deployment.md) · [Validation scope (Chinese)](docs/validation.md).

The project homepage and Linux installation guide are available in Chinese and English. Other maintainer documents retain their existing languages.

The current scope is individual accounts and recent status. Team sharing, long-term history, notifications, and full MIG instance monitoring are not yet available.
