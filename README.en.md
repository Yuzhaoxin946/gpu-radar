# GPU Radar

[简体中文](README.md) | **English**

Check GPU availability across multiple Linux servers from your Mac menu bar.

GPU Radar consists of a Linux collector, a central API, and a native macOS app. Each server gets one menu row: its name, GPU model, and one colored square per GPU. The squares display status without click actions; the desktop window shows utilization, memory, and process details.

Each app account manages its own servers. **New account registration requires an invitation code; ask the service operator privately for one.** Invitations are needed only to create new accounts. Existing account login, password changes, and Agent installation do not need one. An Agent signs in to the app account during installation, then uses an independent device credential for subsequent reports. It does not store the account password.

[Download releases](https://github.com/Yuzhaoxin946/gpu-radar/releases/latest) · [Complete Linux installation and maintenance guide](agent/README.en.md) · [Validation scope (Chinese)](docs/validation.md)

## Where to start

1. **Create an account on your Mac**: obtain an invitation from the service operator, open the desktop window, and register with your username, password, and invitation code. If you do not have the app yet, [download the macOS 1.0 installer](https://github.com/Yuzhaoxin946/gpu-radar/releases/download/v1.0.0/GPU-Radar-1.0.0-macOS-arm64.dmg).
2. **Install an Agent on each GPU server**: SSH into the server normally, then run the installation command below in its interactive terminal. Use the same app account to add servers to one dashboard.
3. **Check your Mac dashboard**: after installation, reports arrive every 5 seconds. New GPUs appear gray until 5 minutes of continuous samples have accumulated.

## Install the Linux Agent

First register with an invitation in the latest Mac app. Existing accounts can go straight to installation. SSH into the GPU server as your usual Linux user and run:

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --username your_account
```

Replace `your_account` with your **app username**. Enter your app password when prompted; input is hidden, and the password does not belong in the command line. The server name defaults to its hostname. Append `--name lab-a` to choose a display name for a new installation.

Requirements: Linux, Bash, curl, Python 3.6+, working `nvidia-smi`, and outbound HTTPS to GitHub and the backend. Both regular users and root can install. The installer resolves the home directory of the Linux account actually running it; it does not use `SUDO_USER` or an inherited `HOME`. Running directly as root or using `sudo bash` on the right side of the pipeline normally installs under `/root`; `sudo -u other bash` installs in `other`'s home. If using sudo, apply it to `bash` (`curl … | sudo bash -s -- …`); applying it only to `curl` does not change the installation user.

On first installation, the default `--background auto` selects an available background mode: regular users use user-level systemd with linger; new root installations prefer system-level systemd without requiring root linger; existing user services keep their current scope. Environments such as Docker without working systemd automatically use a detached background process, so missing boot startup support no longer blocks installation. The installer:

- Downloads and verifies a standalone Agent, with no pip, Docker, or additional Python packages.
- Checks real GPU collection and HTTPS connectivity before signing in and enrolling the server.
- Starts collection every 5 seconds and verifies that the first report is accepted.
- Confirms active/enabled status for systemd services and also checks linger for user services.
- Stores separate configurations and device credentials per host, supporting shared NFS home directories.
- Preserves this host's binding on repeated installation without duplicate enrollment, and attempts to restore the previous installation if an update fails.

**All three modes continue collecting after SSH logout.** Systemd modes configure startup at boot. Detached process mode explicitly reports that restart recovery is not configured: **after a server or container restart, start it manually or integrate it into the container entrypoint / process manager**. On first installation, append `--background process` to choose the detached process explicitly, or `--background systemd` to require systemd and stop if it cannot be configured. Reinstallation preserves the existing mode. To change modes, first follow the stop and removal steps in the full guide; changing the option alone does not migrate an installation. The first connection needs 5 continuous minutes of samples before showing green, yellow, or red. No inbound ports need to be opened on GPU servers.

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

Manage collection as the same Linux user on the target GPU server. The installation result identifies user-level systemd, system-level systemd, or detached process mode. Existing root user services retain their user-level management commands. See the [Linux Agent guide](agent/README.en.md#inspect-and-manage-background-collection) for status, logs, starting, stopping, and removal for each mode.

For Docker / detached process mode, inspect status directly:

```bash
machine_id="$(cat /etc/machine-id)"
agent_file="$HOME/.local/share/gpu-radar/hosts/$machine_id/gpu-radar-agent.pyz"
agent_config="$HOME/.config/gpu-radar/hosts/$machine_id/agent.json"
python3 "$agent_file" background status --config "$agent_config"
tail -n 30 "$agent_config.background.log"
```

Replace `status` with `start` or `stop` to start or stop collection. After a container restart, run `start` if the installation files and configuration were preserved; a background process does not survive a container restart by itself.

To update or retry, rerun the original installation command. It keeps this host's existing binding and background mode without creating another server record. `--username` and `--name` do not change an existing account binding or rename a server; a mismatch stops installation.

Hosts sharing an NFS home directory each need a valid, unique `/etc/machine-id`. The program, configuration, lock, and service are isolated by host. Existing manual installations using `gpu-radar-agent.service` remain unchanged. If the installer detects a legacy flat configuration or service, it stops; the owning host must be verified before migration.

See the [Linux Agent guide](agent/README.en.md) for stopping collection, uninstalling, revoking device credentials, exact paths, and manual installation.

## Common questions

| Symptom | What to do |
| --- | --- |
| Invitation is invalid or fully used | Ask the service operator for a valid invitation. Only a successfully created account consumes a use; installing GPU Agents does not |
| No invitation field in the registration form | Update the repository and rebuild the Mac app; older apps can still sign in to existing accounts |
| GitHub download times out | Use the Aliyun mirror command, including `--mirror aliyun` |
| An interactive terminal is required | Log in to the target GPU host through a normal SSH shell, then paste the command, especially when using a jump server |
| Docker has no systemd / linger is unavailable | A first installation defaults to a detached process that survives SSH logout; restart recovery requires a manual start or container startup integration. Only `--background systemd` requires systemd readiness |
| `Unsafe directory` | Verify the owner and permissions of the exact directory in the error. Fix only a directory you have confirmed you manage; do not recursively change your entire home directory's permissions. See the Linux guide |
| A legacy configuration is detected | Preserve the existing configuration and service until the owning host is verified. Do not copy device credentials to other servers or delete the configuration just to bypass the check |
| GPUs stay gray | Allow 5 continuous minutes initially. If they remain gray, inspect collection logs, the network, and recent reports |
| Must Agents sign in again after an app password change? | No. Devices keep their independent credentials; previous app login sessions are revoked |

## Install the macOS app

[Download GPU Radar 1.0.0 DMG](https://github.com/Yuzhaoxin946/gpu-radar/releases/download/v1.0.0/GPU-Radar-1.0.0-macOS-arm64.dmg) · [Release notes and SHA256SUMS](https://github.com/Yuzhaoxin946/gpu-radar/releases/tag/v1.0.0)

Requires **an Apple Silicon Mac (M-series), macOS 13 or later**. Xcode, Swift, and source code are not needed. An Intel installer is not currently provided.

1. Open the DMG and drag `GPU Radar.app` into `Applications`.
2. Launch the app from Applications, then eject the installation disk.
3. Sign in with an existing account, or register with an invitation. Quit the old app before replacing it during an upgrade. You may need to sign in again; enrolled servers keep their bindings.

This release includes the application icon and uses an ad-hoc signature, **without Developer ID signing or Apple notarization**. macOS may block the first launch. After verifying the download source, follow [Apple’s instructions](https://support.apple.com/en-ph/102445) to allow it in Privacy & Security. There is no need to disable system-wide security checks. The DMG does not provide automatic updates.

## Build the macOS app

Requires macOS 13+, full Xcode, and Swift 5.9+. Run on your **Mac**:

```bash
git clone https://github.com/Yuzhaoxin946/gpu-radar.git
cd gpu-radar
GPU_RADAR_BUILD_WORK="$PWD/.build/macos-build" ./macos/build-app.sh
open "macos/build/GPU Radar.app"
```

If you already cloned the repository, run the last two commands from its root. Run the resulting `macos/build/GPU Radar.app` directly during development. Quit the app before rebuilding and reopening it, and avoid running an older copy in Applications at the same time. Rebuilding may require signing in again.

The app provides invitation-only registration, login, password changes, and demo mode. Changing a password revokes old app login sessions while enrolled GPU servers keep reporting. The menu bar provides an overview, and the desktop window shows server and GPU details.

The desktop window's Add Server sheet provides a one-click install command for the current account, with GitHub and Aliyun download options. The installer prompts for the account password and starts the background collector. Expand the separate login section for an existing, unbound client; a successful one-click installation does not need another login.

The menu panel is 420 points wide with one row per server. Its height grows up to 10 visible rows, then scrolls vertically. GPU squares that exceed a row's width scroll horizontally and have no click actions. Closing the main window keeps the menu overview updating.

The build script uses ad hoc signing and builds for your current Mac architecture. Developer ID signing and Apple notarization are not provided. The GitHub v1.0.0 Release includes an Apple Silicon DMG, the Linux installer, the standalone Agent, and SHA-256 checksums.

### Package a DMG

Maintainers can build from source as above. The build generates and embeds the application icon automatically. Then run:

```bash
./macos/package-dmg.sh
```

The output is `macos/build/GPU-Radar-<version>-macOS-<architecture>.dmg`. Recipients do not need Xcode, Swift, or source code: open the DMG, drag `GPU Radar.app` onto its `Applications` folder, then launch it from Applications. macOS 13 or later is required; choose the architecture matching the Mac processor. `arm64` is for Apple Silicon.

The packaging script preserves the existing app signature; it does not perform Developer ID signing or Apple notarization. Gatekeeper may block the current ad hoc build. Complete [signing and notarization](https://developer.apple.com/macos/distribution/) before general public distribution. A DMG provides installation, not automatic updates. For a manual update, quit the app and replace the previous copy in Applications.

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

First follow [server account management](server/README.md#invitation-only-registration) to create a local invitation, then register a test account through the local API. Alternatively, create a test account directly with the administrator command. `--simulate` explicitly labels simulated data; real collection failures never trigger an automatic simulation fallback.

```bash
./scripts/test.sh
.venv/bin/python scripts/smoke.py
cd macos && swift test
```

More details: [API contract (English)](docs/api-contract.md) · [Deployment guide (Chinese)](docs/deployment.md) · [Validation scope (Chinese)](docs/validation.md).

The project homepage and Linux installation guide are available in Chinese and English. Other maintainer documents retain their existing languages.

The current scope is individual accounts and recent status. Team sharing, long-term history, notifications, and full MIG instance monitoring are not yet available.
