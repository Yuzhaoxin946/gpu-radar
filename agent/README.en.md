# GPU Radar Linux Agent

[简体中文](README.md) | **English** · [Project home](../README.en.md)

Run one Agent on each GPU server to monitor multiple servers from the Mac app. Sign in once with your **GPU Radar app account** to enroll the server; subsequent reports use a separate device credential. The Agent collects metrics only. It does not launch training, reserve GPUs, or allocate resources.

## Before you install

- A Linux GPU server with a working NVIDIA driver. The current SSH user must be able to run `nvidia-smi` and see the GPUs.
- `bash`, `curl`, and **Python 3.6+**. The installer uses a standalone `.pyz`; it needs no pip packages and does not change system Python.
- An account registered in the latest macOS app using an invitation obtained privately from the service operator. Invitations are only used when creating new accounts; existing account login and Agent installation do not need one or consume a use. Your app account is separate from your Linux/SSH account. Other users' training contributes to GPU metrics without requiring those users to register app accounts.
- Outbound HTTPS access to `https://123.56.135.166` and GitHub Release downloads. Use the Aliyun mirror below if GitHub is unreachable. **No new inbound port is needed on the GPU server.**

Open an interactive terminal as the Linux user who will own the installation; both regular users and root are supported. Do not put your app password in a command. Remote command execution through a gateway such as JumpServer may lack `/dev/tty`; log in to the target server's terminal normally before pasting the command. For direct SSH, `ssh -t your-gpu-host` requests a terminal. Docker and other environments without systemd are supported through a detached background process.

## One-command installation

Replace `your_account` with your **app account username**:

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --username your_account
```

Enter your app password when prompted. The installer uses the current cloud service without asking for an IP or port. It verifies the download, collects real GPU data, validates the first report, and starts background collection. The display name defaults to the short hostname; you can choose one explicitly:

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --username your_account --name lab-a
```

If GitHub downloads time out, use the Aliyun mirror of the same client. **Switch both the installer URL and the `--mirror aliyun` argument.**

```bash
curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --username your_account
```

Both download methods connect to `https://123.56.135.166` and use the same app accounts. They do not install a backend on the GPU server.

The installation directory belongs to **the Linux account actually running the installer**. The installer resolves that account's home by effective UID, ignoring `SUDO_USER` and any inherited `HOME` from another account. A regular user installs in their own home; running as root directly or through `sudo bash` normally installs under `/root`. If using sudo, put it on the right side of the pipeline (`curl … | sudo bash -s -- …`); applying sudo only to `curl` does not change the installation user. `curl … | sudo -u other bash -s -- …` installs in `other`'s home. `--username` always names the app account and does not select the Linux installation user.

### Background modes and restart behavior

On first installation, the default `--background auto` chooses a mode for the environment:

| Environment | Background mode | After SSH logout | After server / container restart |
| --- | --- | --- | --- |
| Regular user with working user-level systemd and linger | User-level systemd | Keeps running | Startup at boot is configured |
| New root installation with working system-level systemd | System-level systemd; no root linger needed | Keeps running | Startup at boot is configured |
| Docker or another environment without working systemd | Detached process | Keeps running | Requires manual startup or container startup integration |

Reinstallation preserves the existing background mode and device binding. Existing user services retain their scope, including older root installations; they are not automatically migrated to system services. On first installation, append `--background process` to select a detached process explicitly, or `--background systemd` to require systemd and stop if it cannot be configured. An explicit option that conflicts with the installed mode also stops installation. To change modes, follow the matching stop and removal steps below, then reinstall; do not delete the mode record or lock files directly.

User-level systemd checks and attempts to enable **linger** for the installation user. If administrator privileges are required for a regular user, the installer asks whether to try `sudo`; that prompt requires the **Linux user's password**, not the app password. An administrator can also run `sudo loginctl enable-linger your_linux_username`. On first installation, `auto` falls back to a detached process if this cannot be configured. Forced `systemd` mode or an existing systemd installation reports an error instead of silently changing modes.

The installation result identifies the selected mode and verifies `First report accepted`. Systemd modes also confirm active/enabled service status, with linger checked for user services. Detached process mode explicitly reports **NOT configured for restart**: collection is running in the background, but restart recovery has not been configured.

In Docker, exiting SSH does not stop the detached collector, but stopping or restarting the container terminates its processes. Use `background start` below after restarting, or integrate collection into the container entrypoint / existing process manager. Preserve the client and configuration files, and ensure the container has a valid, stable `/etc/machine-id` that differs from other monitored machines. Do not copy one device credential into multiple containers.

Open the Mac app and sign in to the same app account to see the server. A new device needs a full five minutes of continuous samples before its status colors appear.

### Check hardware and connectivity only

These commands do not sign in, enroll a device, or change installed files or services:

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --check
# If GitHub is unreachable:
curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --check
```

`--check` verifies the client download, real GPU collection, and backend HTTPS. **It does not verify systemd/linger readiness for background operation.** To detect a failed download on the left side of a shell pipeline in automation, first run `set -o pipefail` in Bash. First enrollment still requires an interactive terminal; the installer has no command-line password option.

## Inspect and manage background collection

These commands apply to the one-command installer. Run them in the same Linux user's login shell on the target GPU server, with `HOME` pointing to that account's home. For example, after installing as root through `sudo bash`, use `sudo -i`. Set these variables in each new terminal:

```bash
machine_id="$(cat /etc/machine-id)"
unit="gpu-radar-agent-${machine_id}.service"
agent_dir="$HOME/.local/share/gpu-radar/hosts/$machine_id"
config_dir="$HOME/.config/gpu-radar/hosts/$machine_id"
agent_file="$agent_dir/gpu-radar-agent.pyz"
agent_config="$config_dir/agent.json"
```

Run `python3 "$agent_file" status --config "$agent_config"` to inspect the local binding without revealing the device credential. Confirm ongoing reports using the Mac app's last update time. Choose the section matching the installation result; do not start two background modes at once.

### Docker / detached process

```bash
python3 "$agent_file" background status --config "$agent_config"
tail -n 50 "$agent_config.background.log"
```

| Action | Command |
| --- | --- |
| Start, including after restarting the container | `python3 "$agent_file" background start --config "$agent_config"` |
| Stop | `python3 "$agent_file" background stop --config "$agent_config"` |
| Check the process | `python3 "$agent_file" background status --config "$agent_config"` |

To restart collection, run `stop`, confirm success, then run `start`. Repeated `start` calls do not launch a second collector. `stop` waits up to 25 seconds. The `status` exit codes are 0 for running, 3 for not running, and 1 for identity validation or other errors. This mode does not configure recovery after a server or container restart; a process that exits also needs to be started again. Follow logs with `tail -f "$agent_config.background.log"`; Ctrl+C exits only the log viewer. Logs rotate at 1 MiB, keeping one `.log.1` backup. A container startup script can invoke `background start` using the same absolute file paths; the container still needs its existing main process to stay running. Alternatively, have an existing container process manager supervise `python3 "$agent_file" run --config "$agent_config"`.

Host policies that kill all session processes on logout (for example, `KillUserProcesses=yes`) can also terminate detached workers. Use a system service or an administrator-provided process manager on those hosts.

### User-level systemd (including existing root user services)

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
systemctl --user status "$unit" --no-pager
systemctl --user is-enabled "$unit"
loginctl show-user "$(id -un)" -p Linger
journalctl --user -u "$unit" -n 50 --no-pager
```

### Root system-level systemd

Use this only when the root installation result identifies a system-level service:

```bash
systemctl --system status "$unit" --no-pager
systemctl --system is-enabled "$unit"
journalctl --system -u "$unit" -n 50 --no-pager
```

The systemd operations are the same, with `--user` for user services and `--system` for system services:

| Action | User-level command | System-level command |
| --- | --- | --- |
| Restart collection | `systemctl --user restart "$unit"` | `systemctl --system restart "$unit"` |
| Stop, preserving boot startup | `systemctl --user stop "$unit"` | `systemctl --system stop "$unit"` |
| Start again | `systemctl --user start "$unit"` | `systemctl --system start "$unit"` |
| Stop and disable boot startup | `systemctl --user disable --now "$unit"` | `systemctl --system disable --now "$unit"` |
| Start and enable boot startup | `systemctl --user enable --now "$unit"` | `systemctl --system enable --now "$unit"` |

Replace `-n 50 --no-pager` with `-f` in the journal command to follow logs. Ctrl+C does not stop collection.

### Update or retry installation

Rerun the original installation command. The installer preserves this machine's existing device binding and background mode instead of creating a duplicate. No app password needs to be stored on the server. If an explicitly supplied account or name conflicts with the saved binding, installation stops; use the original account/name when retrying, or omit `--name`.

Download or preflight failures do not stop an existing service. If activation fails, the installer attempts to restore the previous program and service. A newly enrolled configuration is kept for a retry. Changing your app password does not automatically revoke previously enrolled devices.

### Uninstall and remove the device

Set the variables at the start of this section, then follow these steps in order:

1. Stop collection using the installed background mode, disabling boot startup for systemd:

   | Mode | Stop command | Status check |
   | --- | --- | --- |
   | Detached process | `python3 "$agent_file" background stop --config "$agent_config"` | `python3 "$agent_file" background status --config "$agent_config"` |
   | User-level systemd | `systemctl --user disable --now "$unit"` | `systemctl --user is-active "$unit"` |
   | System-level systemd | `systemctl --system disable --now "$unit"` | `systemctl --system is-active "$unit"` |

   Confirm collection has stopped before continuing. Systemd's `is-active` returning `inactive` with a nonzero exit status is expected. If you integrated collection into a container startup script or another process manager, remove that startup configuration too.

2. Delete this server in the Mac app to revoke its server-side device credential and remove its dashboard record. Stopping collection or running `logout` alone does not revoke the server-side credential.

3. For systemd, remove the matching unit file and reload. Run only the pair matching your installation; skip this step for a detached process:

   ```bash
   # User-level (including existing root user services)
   rm -f "$HOME/.config/systemd/user/$unit"
   systemctl --user daemon-reload
   ```

   ```bash
   # System-level (root system service installations only)
   rm -f "/etc/systemd/system/$unit"
   systemctl --system daemon-reload
   ```

4. After confirming that collection stopped successfully, remove the local binding, program, and process logs. Do not remove control files or locks while collection is running. The first command validates the machine ID, and `&&` proceeds only if the previous command succeeds. `rmdir` removes only empty directories, not other machines from a shared home:

   ```bash
   python3 -c 'import re, sys; sys.exit(0 if re.fullmatch(r"[0-9a-f]{32}", sys.argv[1]) and sys.argv[1] != "0" * 32 else "Invalid machine ID")' "$machine_id" && \
     python3 "$agent_file" logout --config "$agent_config" && \
     rm -f "$agent_file" "$agent_dir/.install.lock" "$agent_dir/background-mode" "$agent_config.lock" \
       "$agent_config.background.json" "$agent_config.background.lock" "$agent_config.background.stop" \
       "$agent_config.background.log" "$agent_config.background.log.1" && \
     rmdir "$agent_dir" "$config_dir"
   ```

Always pass this machine's explicit `--config` path; a bare `logout` uses the legacy default path. Do not disable linger for the installation user, because other user services may depend on it. To enroll again or switch app accounts, complete this removal process and rerun the installer.

## Storage paths and shared home directories

The installer stores separate files for each host using `/etc/machine-id`. Here, `~` means the home directory of the Linux account actually running the installer; root follows the same rule:

| Item | Path |
| --- | --- |
| Client | `~/.local/share/gpu-radar/hosts/<machine-id>/gpu-radar-agent.pyz` |
| Device configuration | `~/.config/gpu-radar/hosts/<machine-id>/agent.json`, mode 0600 |
| User service (when selected) | `~/.config/systemd/user/gpu-radar-agent-<machine-id>.service` |
| System service (when selected) | `/etc/systemd/system/gpu-radar-agent-<machine-id>.service` |
| Selected background mode | `background-mode` in the client directory |
| Detached process state / control | Device configuration path with `.background.json`, `.background.lock`, and `.background.stop` suffixes |
| Detached process logs | `agent.json.background.log` beside the device configuration; up to 1 MiB, with one `.log.1` backup |

Multiple GPU servers sharing an NFS home therefore keep separate device credentials. The unit's `ConditionHost` allows it to start only on the corresponding machine. Every host must have a valid, unique `/etc/machine-id`; administrators cloning systems should ensure this.

The configuration stores the device token, backend address, device identifier, account label, and related settings. **It does not store your app password.** Do not copy it to another server or commit it to GitHub.

If the installer finds a legacy flat `~/.config/gpu-radar/agent.json` or `gpu-radar-agent.service`, it stops and leaves the existing installation unchanged. A shared home does not reliably identify which machine owns those old credentials. Existing services can continue to run. Contact the maintainer for a migration after confirming the original host; **do not delete or copy old credentials to bypass the check**.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| GitHub download times out | Use the complete Aliyun mirror command above, including `--mirror aliyun` |
| `An interactive SSH terminal is required` | Open a normal terminal on the target server, or use `ssh -t` for direct SSH; avoid gateway remote-execution modes without a TTY |
| `nvidia-smi is missing` / real collection fails | Run `nvidia-smi` as the same user; ask an administrator to fix the driver, PATH, or device read permissions |
| `Reboot startup was NOT enabled` (older installer) | Download the current installer again. Docker defaults to a detached process; `--background process` selects it explicitly |
| `Automatic startup ... NOT configured` | A detached collector is running, but restart recovery is not configured. Run `background start` after a restart, or integrate with container startup |
| `systemd user manager is unavailable` / `Failed to connect to bus` | If systemd is required, ask an administrator to check linger and the user manager, reconnect, and set the variables above. Docker can use the default auto mode or process mode; environment variables alone cannot create systemd |
| Installation is in an unexpected directory | The installer uses the executing account's home; check whether sudo was applied to `bash`. `--username` names the app account and does not switch Linux users |
| `Unsafe directory` | Inspect the ownership and permissions of the **specific directory** named in the error; see below |
| Legacy binding, account, or name mismatch | Preserve the configuration. Use the existing binding's parameters or follow the removal procedure above; contact the maintainer before migrating a legacy installation |
| Device credential rejected (401/403) | Check whether the device was deleted in the app. The service stops rather than restarting endlessly. Follow removal and reinstallation steps if it needs a new binding |
| A new device is initially gray | Wait for five minutes of continuous data. If it stays gray, inspect the last report time and logs |

The installer requires your home and the subdirectories it uses to be owned by your Linux user, free of symlinks, and not writable by the group or others. For `Unsafe directory`, inspect the exact directory mentioned in the error, for example:

```bash
ls -ld "$HOME"
```

Only if the error refers to your own home and it does not need group/other write access, run `chmod go-w "$HOME"` on that one directory. For other directories, adjust only the exact reported path. Ask an administrator about intentionally shared directories, incorrect owners, or symlinks. **Do not run `chmod -R` or recursive `chown` on your entire home.** If the error specifically says the device configuration is too permissive, use `chmod 600 "$agent_config"` for this machine's configuration. Do not print or paste its token.

## Advanced: build from source and install manually

The one-command installer is sufficient for typical use. The manual examples below use the legacy default paths and are suitable only for **one machine without a shared home**. They do not automatically create a machine-id-isolated installation. For a self-hosted backend, replace `https://radar.example.org` with its HTTPS address.

### Python 3.6+ standalone file

Build on a development machine from the repository root. The build uses only the standard library:

```bash
python3 agent/build-zipapp.py
# Produces agent/gpu-radar-agent.pyz; copy it to the GPU server.
```

Run it directly on the GPU server without pip:

```bash
python3 gpu-radar-agent.pyz login \
  --server https://radar.example.org --name lab-a --username your_account
python3 gpu-radar-agent.pyz status
python3 gpu-radar-agent.pyz run
```

If the NVML Python package is absent, the Agent uses the existing `nvidia-smi`. To inspect real hardware without an account or network access, run `python3 gpu-radar-agent.pyz collect --collection-timeout 8`. Standard output contains one JSON sample; success exits with 0, while collection errors still emit error JSON and exit with 2. The diagnostic `collect` command timestamps samples with the local clock; normal reporting uses the server time synchronization described below.

### Python 3.10+ pip installation

After copying the repository onto the GPU server, run from its root:

```bash
python3 -m venv "$HOME/.local/share/gpu-radar/venv"
"$HOME/.local/share/gpu-radar/venv/bin/pip" install './agent[nvml]'
"$HOME/.local/share/gpu-radar/venv/bin/gpu-radar-agent" login \
  --server https://radar.example.org --name lab-a --username your_account
"$HOME/.local/share/gpu-radar/venv/bin/gpu-radar-agent" status
"$HOME/.local/share/gpu-radar/venv/bin/gpu-radar-agent" run
```

Register an account in the latest Mac app with an invitation first; a backend administrator may also disable all registration. Obtain invitations privately from the service operator. Only successfully creating a new account consumes a use; Agent login and device enrollment do not require an invitation. After login, the Agent revokes its temporary account session and saves the device configuration to `~/.config/gpu-radar/agent.json` with mode 0600. Commands using a configuration accept `--config /path/agent.json` before or after the subcommand. The configuration must belong to the Linux user running the Agent.

### Manual systemd user service

On a machine without a shared home, complete the venv installation and login above, then run from the repository root:

```bash
mkdir -p "$HOME/.config/systemd/user"
cp agent/systemd/gpu-radar-agent.user.service \
  "$HOME/.config/systemd/user/gpu-radar-agent.service"
systemctl --user daemon-reload
systemctl --user enable --now gpu-radar-agent.service
systemctl --user status gpu-radar-agent.service
journalctl --user -u gpu-radar-agent.service -f
loginctl show-user "$(id -un)" -p Linger
```

Linger must be enabled to keep running after logout and start after reboot. If needed, ask an administrator to run `sudo loginctl enable-linger your_linux_username`. A foreground `run` command alone does not guarantee operation after SSH logout.

For `.pyz`, replace the template's `ExecStart` with absolute Python and client paths, for example:

```ini
ExecStart=/usr/bin/python3 %h/.local/share/gpu-radar/gpu-radar-agent.pyz run --config %h/.config/gpu-radar/agent.json
```

Without systemd, the installer defaults to a detached background process. Manual deployments can also use `background start --config /path/agent.json`. Recovery after a container restart requires integration with its entrypoint or an existing process manager.

### Administrator-managed system service

[`systemd/gpu-radar-agent.system.service`](systemd/gpu-radar-agent.system.service) is a template. An administrator must adjust `User`, `Group`, `ExecStart`, and `ReadWritePaths`:

1. Install the package in `/opt/gpu-radar/venv`, using a dedicated non-root Linux service user with NVIDIA device read permissions.
2. Create `/var/lib/gpu-radar`, owned by that service user with mode 0700.
3. **As the service user**, run an interactive `login --config /var/lib/gpu-radar/agent.json ...` and verify configuration mode 0600.
4. Place the adjusted template at `/etc/systemd/system/gpu-radar-agent.service`, then run `systemctl --system daemon-reload` and `systemctl --system enable --now gpu-radar-agent`.

The system service template is for manual setup; it does not automatically modify system accounts, sudo policy, or firewall rules.

## Collection and failure behavior

- Samples are collected every five seconds by default. The backend decides green/yellow/red/gray status; the Agent does not decide whether a GPU is idle.
- The Agent prefers `nvidia-ml-py` (NVML), with `nvidia-smi` as a fallback. NVML runs in a separate process with an eight-second default hard timeout; individual `nvidia-smi` commands have a three-second limit. Missing GPUs, blocked drivers, or failed commands produce collection errors, never implicit simulation or all-zero readings.
- At startup and every five minutes, `run` reads `server_time` from `/api/v1/health`, estimates server time using round-trip latency, and advances it with a monotonic clock. Reports use collection start time. It does not modify the system clock. Synchronization failures pause reporting and trigger retries instead of falling back to stale or local timestamps.
- Memory is measured in MiB. Unavailable metrics are `null`; failed process enumeration sets `processes_known=false`. The Agent reads `/proc/PID/comm` and the owning UID, not full command lines, environment variables, or training arguments. Process metadata inaccessible for other users is unknown.
- MIG instance monitoring is not currently supported. When MIG is enabled or its status cannot be determined, process visibility is unknown; physical GPU metrics are collected where available.
- Network failures use exponential backoff with jitter, capped at approximately 72 seconds. Failed samples are discarded; recovery collects fresh data so an old idle reading cannot overwrite current activity. The backend determines offline status from the last valid data time.
- HTTPS certificates are verified, and credentials are not sent through redirects. Loopback HTTP is allowed for local development; other HTTP requires an explicit `--allow-insecure-http` at login and should not be used with the current cloud service.
- Device tokens can report only for their enrolled server. After a device is deleted in the Mac app, a 401/403 response stops the Agent; systemd does not repeatedly restart a revoked device.

## Development and explicit simulation

Enroll a separate device named `Demo · Simulation` with its own configuration for demonstrations. Log in using that configuration first, then run:

```bash
gpu-radar-agent run --config /path/demo-agent.json --simulate
# Send one report only; color classification still needs a full five-minute window.
gpu-radar-agent run --config /path/demo-agent.json --simulate --once
```

Simulated hostnames, GPU names, and UUIDs contain a `SIMULATION` marker. The demo includes four GPUs representing low activity, busy compute, occupied memory, and unknown metrics. Real mode never enables simulation automatically. Two Agents cannot run concurrently using the same configuration.

Source execution and tests, from the repository root:

```bash
PYTHONPATH=agent/src python3 -m gpu_radar_agent --help
PYTHONPATH=agent/src python3 -m unittest discover -s agent/tests -v
```

NVIDIA reference: [nvidia-smi and NVML](https://docs.nvidia.com/deploy/nvidia-smi/index.html). Validate driver compatibility, distribution support, and service lifecycle on each target server.
