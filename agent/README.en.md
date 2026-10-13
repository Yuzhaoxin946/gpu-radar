# GPU Radar Linux Agent

[简体中文](README.md) | **English** · [Project home](../README.en.md)

Run one Agent on each GPU server to monitor multiple servers from the Mac app. Sign in once with your **GPU Radar app account** to enroll the server; subsequent reports use a separate device credential. The Agent collects metrics only. It does not launch training, reserve GPUs, or allocate resources.

## Before you install

- A Linux GPU server with a working NVIDIA driver. The current SSH user must be able to run `nvidia-smi` and see the GPUs.
- `bash`, `curl`, **Python 3.6+**, and a working systemd user manager. The installer uses a standalone `.pyz`; it needs no pip packages and does not change system Python.
- An account registered in the latest macOS app using an invitation obtained privately from the service operator. Invitations are only used when creating new accounts; existing account login and Agent installation do not need one or consume a use. Your app account is separate from your Linux/SSH account. Other users' training contributes to GPU metrics without requiring those users to register app accounts.
- Outbound HTTPS access to `https://123.56.135.166` and GitHub Release downloads. Use the Aliyun mirror below if GitHub is unreachable. **No new inbound port is needed on the GPU server.**

Open an interactive terminal as your normal SSH user, then run the command on the GPU server. Do not prefix the installer with `sudo` or put your app password in a command. Remote command execution through a gateway such as JumpServer may lack `/dev/tty`; log in to the target server's terminal normally before pasting the command. For direct SSH, `ssh -t your-gpu-host` requests a terminal.

## One-command installation

Replace `your_account` with your **app account username**:

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --username your_account
```

Enter your app password when prompted. The installer uses the current cloud service without asking for an IP or port. It verifies the download, collects real GPU data, validates the first report, and enables the background service. The display name defaults to the short hostname; you can choose one explicitly:

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --username your_account --name lab-a
```

If GitHub downloads time out, use the Aliyun mirror of the same client. **Switch both the installer URL and the `--mirror aliyun` argument.**

```bash
curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --username your_account
```

Both download methods connect to `https://123.56.135.166` and use the same app accounts. They do not install a backend on the GPU server.

The installer checks and attempts to enable **linger** for your Linux user. This keeps the service running after SSH logout and starts it at boot. If server policy requires administrator privileges, it asks whether to try `sudo`. Any sudo password prompt requires your **Linux user's password**, not your app password. If you lack permission, ask an administrator to run:

```bash
sudo loginctl enable-linger your_linux_username
```

Replace `your_linux_username` with the actual SSH username, reconnect over SSH, and rerun the installer. Background installation and the first report are confirmed only when it reports `service active and enabled; Linger=yes` and `First report accepted`.

Open the Mac app and sign in to the same app account to see the server. A new device needs a full five minutes of continuous samples before its status colors appear.

### Check hardware and connectivity only

These commands do not sign in, enroll a device, or change installed files or services:

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --check
# If GitHub is unreachable:
curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --check
```

`--check` verifies the client download, real GPU collection, and backend HTTPS. **It does not verify systemd/linger readiness for background operation.** To detect a failed download on the left side of a shell pipeline in automation, first run `set -o pipefail` in Bash. First enrollment still requires an interactive terminal; the installer has no command-line password option.

## Inspect and manage the background service

The following commands apply to the one-command installer. Run them on the target GPU server as the same Linux user. Set these variables in each new terminal:

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
machine_id="$(cat /etc/machine-id)"
unit="gpu-radar-agent-${machine_id}.service"
agent_dir="$HOME/.local/share/gpu-radar/hosts/$machine_id"
config_dir="$HOME/.config/gpu-radar/hosts/$machine_id"
agent_file="$agent_dir/gpu-radar-agent.pyz"
agent_config="$config_dir/agent.json"
```

Inspect the process, boot startup, linger, recent logs, and local binding:

```bash
systemctl --user status "$unit" --no-pager
systemctl --user is-enabled "$unit"
loginctl show-user "$(id -un)" -p Linger
journalctl --user -u "$unit" -n 50 --no-pager
python3 "$agent_file" status --config "$agent_config"
```

The `status` subcommand does not reveal the device credential, but checks only the local binding. Confirm continuing reports using the last update time in the Mac app. Follow logs with `journalctl --user -u "$unit" -f`; pressing Ctrl+C exits the log viewer without stopping collection.

| Action | Command | Effect |
| --- | --- | --- |
| Restart | `systemctl --user restart "$unit"` | Restarts collection, preserving the account binding and boot startup |
| Stop temporarily | `systemctl --user stop "$unit"` | Stops now; the service may still start at the next boot |
| Start again | `systemctl --user start "$unit"` | Resumes the installed collection service |
| Stop and disable boot startup | `systemctl --user disable --now "$unit"` | Stops immediately and disables startup at boot |
| Restore background operation and boot startup | `systemctl --user enable --now "$unit"` | Starts the service and enables it at boot |

### Update or retry installation

Rerun the original installation command. The installer preserves this machine's existing device binding instead of creating a duplicate. No app password needs to be stored on the server. If an explicitly supplied account or name conflicts with the saved binding, installation stops; use the original account/name when retrying, or omit `--name`.

Download or preflight failures do not stop an existing service. If activation fails, the installer attempts to restore the previous program and service. A newly enrolled configuration is kept for a retry. Changing your app password does not automatically revoke previously enrolled devices.

### Uninstall and remove the device

First set the variables at the start of this section, then follow these steps in order:

1. Stop this machine's service and disable boot startup:

   ```bash
   systemctl --user disable --now "$unit"
   systemctl --user is-active "$unit"
   ```

   Confirm it is no longer running before continuing. An `inactive` result and nonzero exit status from `is-active` are expected.

2. Delete this server in the Mac app to revoke its server-side device credential and remove its dashboard record. Stopping the local service or running `logout` alone does not revoke the server-side credential.

3. Remove the local binding, program, and unit file. The first command validates the machine ID, and `&&` proceeds only if the previous command succeeds. `rmdir` removes only empty directories; it does not recursively remove other machines from a shared home:

   ```bash
   python3 -c 'import re, sys; sys.exit(0 if re.fullmatch(r"[0-9a-f]{32}", sys.argv[1]) and sys.argv[1] != "0" * 32 else "Invalid machine ID")' "$machine_id" && \
     python3 "$agent_file" logout --config "$agent_config" && \
     rm -f "$HOME/.config/systemd/user/$unit" "$agent_file" && \
     systemctl --user daemon-reload && \
     rm -f "$agent_dir/.install.lock" "$agent_config.lock" && \
     rmdir "$agent_dir" "$config_dir"
   ```

Always pass this machine's explicit `--config` path. A bare `logout` uses the legacy default path. Do not disable linger for the Linux user, because other user services may depend on it. To enroll again or switch app accounts, complete this removal process and rerun the installer.

## Storage paths and shared home directories

The installer stores separate files for each host using `/etc/machine-id`:

| Item | Path |
| --- | --- |
| Client | `~/.local/share/gpu-radar/hosts/<machine-id>/gpu-radar-agent.pyz` |
| Device configuration | `~/.config/gpu-radar/hosts/<machine-id>/agent.json`, mode 0600 |
| User service | `~/.config/systemd/user/gpu-radar-agent-<machine-id>.service` |

Multiple GPU servers sharing an NFS home therefore keep separate device credentials. The unit's `ConditionHost` allows it to start only on the corresponding machine. Every host must have a valid, unique `/etc/machine-id`; administrators cloning systems should ensure this.

The configuration stores the device token, backend address, device identifier, account label, and related settings. **It does not store your app password.** Do not copy it to another server or commit it to GitHub.

If the installer finds a legacy flat `~/.config/gpu-radar/agent.json` or `gpu-radar-agent.service`, it stops and leaves the existing installation unchanged. A shared home does not reliably identify which machine owns those old credentials. Existing services can continue to run. Contact the maintainer for a migration after confirming the original host; **do not delete or copy old credentials to bypass the check**.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| GitHub download times out | Use the complete Aliyun mirror command above, including `--mirror aliyun` |
| `An interactive SSH terminal is required` | Open a normal terminal on the target server, or use `ssh -t` for direct SSH; avoid gateway remote-execution modes without a TTY |
| `nvidia-smi is missing` / real collection fails | Run `nvidia-smi` as the same user; ask an administrator to fix the driver, PATH, or device read permissions |
| `Reboot startup was NOT enabled` | Ask an administrator to enable linger for the actual Linux user, then rerun the installer |
| `systemd user manager is unavailable` / `Failed to connect to bus` | Confirm linger is enabled, log out and reconnect, and set the two environment variables above. If it still fails, ask an administrator to check the systemd user manager. Setting variables alone does not create it |
| `HOME differs from your Linux account home` | Use a normal SSH login without overriding HOME or running the installer through sudo |
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

If there is no systemd user manager, an administrator must use another existing process manager to run the same `run` command. The installer does not automatically choose an alternative background mechanism.

### Administrator-managed system service

[`systemd/gpu-radar-agent.system.service`](systemd/gpu-radar-agent.system.service) is a template. An administrator must adjust `User`, `Group`, `ExecStart`, and `ReadWritePaths`:

1. Install the package in `/opt/gpu-radar/venv`, using a dedicated non-root Linux service user with NVIDIA device read permissions.
2. Create `/var/lib/gpu-radar`, owned by that service user with mode 0700.
3. **As the service user**, run an interactive `login --config /var/lib/gpu-radar/agent.json ...` and verify configuration mode 0600.
4. Place the adjusted template at `/etc/systemd/system/gpu-radar-agent.service`, then run `systemctl daemon-reload` and `systemctl enable --now gpu-radar-agent`.

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
