# GPU Radar Linux Agent

**简体中文** | [English](README.en.md) · [项目主页](../README.md)

在每台 GPU 服务器运行一个 Agent，就能在 Mac App 中查看多台服务器的 GPU 状态。首次安装用 **GPU Radar App 账户**登录，设备随后使用独立凭据上报。Agent 只采集指标，不启动训练、不占用或分配 GPU。

## 安装前准备

- Linux GPU 服务器，有正常工作的 NVIDIA 驱动，当前 SSH 用户执行 `nvidia-smi` 能看到显卡。
- `bash`、`curl`、**Python 3.6+**，以及可用的用户级 systemd。安装器使用独立 `.pyz`，不需要 pip，不修改系统 Python。
- 在 macOS App 中注册账户。App 账户与 Linux/SSH 账户是两回事；一台服务器上其他人的训练也会计入 GPU 指标，不要求他们注册 App。
- 能通过 HTTPS 出站访问 `https://123.56.135.166`，以及 GitHub Release 下载地址；GitHub 不通时用下方阿里云镜像。**GPU 服务器不需要新增入站端口。**

使用日常 SSH 用户打开交互式终端，再在服务器上执行命令。不要给安装命令加 `sudo`，也不要把 App 密码写进命令行。通过 JumpServer 等跳板机时，远程执行模式可能没有 `/dev/tty`；请先正常登录目标服务器的终端，再粘贴安装命令。直接 SSH 可用 `ssh -t your-gpu-host` 请求终端。

## 一键安装

先把 `your_account` 替换成你的 **App 账户名**：

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --username your_account
```

按提示输入 App 密码。安装器默认使用当前云端服务，无需填写 IP 或端口；它会校验下载文件、采集真实 GPU 数据、验证首次上报，然后启用后台服务。服务器名称默认是短主机名，也可以自行指定：

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --username your_account --name lab-a
```

如果 GitHub 下载超时，换用同一客户端的阿里云镜像。**两个位置都要切换：安装脚本地址和 `--mirror aliyun` 参数。**

```bash
curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --username your_account
```

两种下载方式都固定连接 `https://123.56.135.166`，使用同一个 App 账户体系；不会在 GPU 服务器上部署服务端。

安装器检查并尝试开启 Linux 用户的 `linger`，让服务在退出 SSH 后继续运行，并在机器启动时自动运行。如果服务器策略要求管理员权限，会提示是否通过 `sudo` 开启；此时 sudo 如询问密码，要求的是 **Linux 用户密码**，不是 App 密码。没有权限时，请让管理员执行：

```bash
sudo loginctl enable-linger your_linux_username
```

将 `your_linux_username` 替换为实际 SSH 用户名，然后重新登录 SSH 并重跑安装器。只有安装器报告 `service active and enabled; Linger=yes` 和 `First report accepted` 才表示后台安装及首次上报检查成功。

打开 Mac App 并登录同一 App 账户，即可看到服务器。新接入设备需要积累完整 5 分钟的连续数据后才显示状态颜色。

### 只检查硬件和网络

不登录 App、不登记设备、不修改已安装文件或服务：

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --check
# GitHub 不通时：
curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --check
```

`--check` 会验证客户端下载、真实 GPU 采集和后端 HTTPS；**不检查 systemd/linger 是否已具备后台运行条件**。如需在脚本中准确捕获管道左侧的下载失败，先在 Bash 中执行 `set -o pipefail`。首次登记仍需要交互式终端，安装器没有命令行密码选项。

## 查看状态和管理后台服务

以下命令适用于一键安装器。在目标 GPU 服务器、同一个 Linux 用户下运行；每次新开终端先设置这些变量：

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

查看进程、开机自启、linger、最近日志和本地账户绑定：

```bash
systemctl --user status "$unit" --no-pager
systemctl --user is-enabled "$unit"
loginctl show-user "$(id -un)" -p Linger
journalctl --user -u "$unit" -n 50 --no-pager
python3 "$agent_file" status --config "$agent_config"
```

`status` 子命令不会显示设备凭据，但只检查本地绑定；是否持续上报应以 Mac App 中的最近更新时间为准。实时跟随日志可用 `journalctl --user -u "$unit" -f`，按 Ctrl+C 退出日志查看不会停止采集。

| 操作 | 命令 | 效果 |
| --- | --- | --- |
| 重启 | `systemctl --user restart "$unit"` | 重启采集，保留账户绑定和开机自启 |
| 暂时停止 | `systemctl --user stop "$unit"` | 当前停止；仍可在下次开机时启动 |
| 重新启动 | `systemctl --user start "$unit"` | 恢复已安装的采集服务 |
| 停止并关闭自启 | `systemctl --user disable --now "$unit"` | 立即停止，之后不再开机启动 |
| 恢复后台和自启 | `systemctl --user enable --now "$unit"` | 恢复后台服务和开机启动 |

### 更新或重试安装

重新执行原来的一键安装命令即可。安装器会保留本机现有设备绑定，不重复注册设备；无需把密码保存在服务器上。已有绑定的账户或名称与显式提供的参数不一致时会停止，因此重试时使用原账户名和原名称，或不传 `--name`。

下载或预检查失败不会停止现有服务；激活失败会尝试恢复原程序和服务，新登记的配置会保留供重试使用。修改 App 密码不会自动撤销已经绑定的设备。

### 卸载并删除设备

先运行本节开头的变量设置，再按顺序操作：

1. 停止本机服务并关闭自启：

   ```bash
   systemctl --user disable --now "$unit"
   systemctl --user is-active "$unit"
   ```

   确认服务已不再运行后继续。`is-active` 显示 `inactive` 并返回非零退出码是预期现象。

2. 在 Mac App 中删除这台服务器，撤销其服务端设备凭据并清理看板记录。只停止本机服务或执行 `logout` 不会撤销服务端凭据。

3. 清理本机绑定、程序和服务文件。第一条命令验证 machine-id，`&&` 确保前一步成功才继续；`rmdir` 只删除空目录，不会递归删除共享 Home 中的其他主机：

   ```bash
   python3 -c 'import re, sys; sys.exit(0 if re.fullmatch(r"[0-9a-f]{32}", sys.argv[1]) and sys.argv[1] != "0" * 32 else "Invalid machine ID")' "$machine_id" && \
     python3 "$agent_file" logout --config "$agent_config" && \
     rm -f "$HOME/.config/systemd/user/$unit" "$agent_file" && \
     systemctl --user daemon-reload && \
     rm -f "$agent_dir/.install.lock" "$agent_config.lock" && \
     rmdir "$agent_dir" "$config_dir"
   ```

请务必传入上面的本机 `--config`；直接运行裸 `logout` 会使用旧版默认路径。无需关闭该 Linux 用户的 linger，因为其他用户级服务也可能依赖它。要重新绑定或改用其他 App 账户，完成以上删除流程后重新运行安装器。

## 存储路径与共享 Home

安装器按 `/etc/machine-id` 为每台主机保存独立文件：

| 内容 | 路径 |
| --- | --- |
| 客户端 | `~/.local/share/gpu-radar/hosts/<machine-id>/gpu-radar-agent.pyz` |
| 设备配置 | `~/.config/gpu-radar/hosts/<machine-id>/agent.json`，权限 0600 |
| 用户服务 | `~/.config/systemd/user/gpu-radar-agent-<machine-id>.service` |

因此多台 GPU 服务器共享 NFS Home 时，也不会共用同一台设备的凭据。服务中的 `ConditionHost` 限定它只能在对应机器启动。每台主机的 `/etc/machine-id` 必须有效且唯一；克隆系统的管理员应确保这一点。

配置里只有设备 token、后端地址、设备标识及账户标签等，**不保存 App 密码**。不要复制配置到其他服务器，也不要把它提交到 GitHub。

发现旧版平铺的 `~/.config/gpu-radar/agent.json` 或 `gpu-radar-agent.service` 时，安装器会停止并保留旧安装，因为共享 Home 下无法可靠判断旧凭据属于哪台机器。原服务可继续使用；请联系维护者确认主机后迁移，**不要删除或复制旧凭据来绕过检查**。

## 常见问题

| 现象 | 处理方法 |
| --- | --- |
| GitHub 下载超时 | 使用上面的阿里云镜像完整命令，包含 `--mirror aliyun` |
| `An interactive SSH terminal is required` | 正常登录目标服务器终端后重试，或直接 SSH 使用 `ssh -t`；避免跳板机无 TTY 的远程执行模式 |
| `nvidia-smi is missing` / 真实采集失败 | 先在同一用户下执行 `nvidia-smi`，请管理员修复驱动、PATH 或设备读取权限 |
| `Reboot startup was NOT enabled` | 请管理员为实际 Linux 用户开启 linger，再重跑安装器 |
| `systemd user manager is unavailable` / `Failed to connect to bus` | 确认 linger 已开启，退出后重新登录，设置上文两个环境变量；仍失败需管理员检查用户级 systemd。变量本身不会创建服务管理器 |
| `HOME differs from your Linux account home` | 使用正常 SSH 登录，不要覆写 HOME，也不要以 sudo 运行安装器 |
| `Unsafe directory` | 检查报错中**具体目录**的所有者和权限，见下文 |
| 旧版绑定、名称或账户不匹配 | 保留原配置；使用原绑定参数，或按上面的正式卸载流程处理，旧版安装应先联系维护者迁移 |
| 设备凭据被拒绝（401/403） | 在 App 确认设备是否已被删除；服务会停止而不会无限重启。需要重新绑定时按删除与重装流程处理 |
| 新设备暂时灰色 | 等待完整 5 分钟连续数据；持续灰色则检查最近上报时间和日志 |

安装器要求 Home 及使用的子目录属于当前 Linux 用户，不能是符号链接，且不能允许组或其他用户写入。遇到 `Unsafe directory`，先查看报错中那个目录，例如：

```bash
ls -ld "$HOME"
```

只有确认报错针对自己的 Home、且它不需要组/其他用户写入时，才对这一个目录执行 `chmod go-w "$HOME"`；其他目录同样只调整报错的确切路径。共享目录、错误所有者或符号链接应请管理员确认。**不要使用 `chmod -R` 或递归 `chown` 修改整个 Home。** 如明确报错为设备配置权限过宽，对本机配置执行 `chmod 600 "$agent_config"` 即可，不要输出或粘贴其中的 token。

## 高级：源码构建与手动安装

一般使用上面的一键安装器即可。以下手动示例使用旧版默认路径，只适用于**不共享 Home 的单台主机**；它们不会自动创建按 machine-id 隔离的安装。自建服务端时，将示例中的 `https://radar.example.org` 替换为你的 HTTPS 地址。

### Python 3.6+ 独立文件

在开发机器上，从项目根目录构建；构建只使用标准库：

```bash
python3 agent/build-zipapp.py
# 生成 agent/gpu-radar-agent.pyz，将其复制到 GPU 服务器。
```

在 GPU 服务器上直接运行，无需 pip：

```bash
python3 gpu-radar-agent.pyz login \
  --server https://radar.example.org --name lab-a --username your_account
python3 gpu-radar-agent.pyz status
python3 gpu-radar-agent.pyz run
```

缺少 NVML Python 包时自动使用已有 `nvidia-smi`。只检查硬件、不登录和联网，可执行 `python3 gpu-radar-agent.pyz collect --collection-timeout 8`；标准输出为一个 JSON 样本，成功退出码为 0，采集错误也输出错误 JSON 并以 2 退出。`collect` 的 `sampled_at` 是本机时钟，只用于诊断；正式上报会进行下文的服务端时间同步。

### Python 3.10+ pip 安装

在已经复制项目的 GPU 服务器上，从项目根目录执行：

```bash
python3 -m venv "$HOME/.local/share/gpu-radar/venv"
"$HOME/.local/share/gpu-radar/venv/bin/pip" install './agent[nvml]'
"$HOME/.local/share/gpu-radar/venv/bin/gpu-radar-agent" login \
  --server https://radar.example.org --name lab-a --username your_account
"$HOME/.local/share/gpu-radar/venv/bin/gpu-radar-agent" status
"$HOME/.local/share/gpu-radar/venv/bin/gpu-radar-agent" run
```

账户先在 Mac App 注册，服务端也可以关闭公开注册。登录完成后撤销临时账户会话，设备配置默认保存在 `~/.config/gpu-radar/agent.json`（0600）。所有使用配置的子命令都可以在子命令前后指定 `--config /path/agent.json`。配置与运行 Agent 的 Linux 用户必须一致。

### 手动用户级 systemd

在不共享 Home 的机器完成以上 venv 安装和登录后，从项目根目录执行：

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

要退出 SSH 和重启后仍运行，需要开启 linger，必要时让管理员执行 `sudo loginctl enable-linger your_linux_username`。单独以前台 `run` 启动不能保证退出 SSH 后继续运行。

使用 `.pyz` 时，将模板的 `ExecStart` 换成 Python 和客户端的绝对路径，例如：

```ini
ExecStart=/usr/bin/python3 %h/.local/share/gpu-radar/gpu-radar-agent.pyz run --config %h/.config/gpu-radar/agent.json
```

没有用户级 systemd 的机器，需要管理员用其他已有进程管理器运行相同的 `run` 命令；一键安装器不会自动选择其他后台方式。

### 管理员配置系统级 systemd

[`systemd/gpu-radar-agent.system.service`](systemd/gpu-radar-agent.system.service) 是模板，管理员需调整 `User`、`Group`、`ExecStart`、`ReadWritePaths`：

1. 在 `/opt/gpu-radar/venv` 安装本包，使用专门的非 root Linux 服务用户，并授予读取 NVIDIA 设备所需权限。
2. 创建 `/var/lib/gpu-radar`，归服务用户所有，权限 0700。
3. **以服务用户**交互式执行 `login --config /var/lib/gpu-radar/agent.json ...`，确认配置权限 0600。
4. 把调整好的模板放到 `/etc/systemd/system/gpu-radar-agent.service`，执行 `systemctl daemon-reload` 和 `systemctl enable --now gpu-radar-agent`。

系统级模板只供手工配置，不会自动修改系统账户、sudo 配置或防火墙。

## 采集与故障行为

- 默认每 5 秒采集；后端统一判定绿/黄/红/灰，Agent 不自行判断空闲。
- 优先使用 `nvidia-ml-py`（NVML），不可用时尝试 `nvidia-smi`。NVML 在独立子进程中执行，默认 8 秒硬超时；`nvidia-smi` 单次命令限时 3 秒。无 GPU、驱动阻塞或命令失败会上报采集错误，不会变成模拟数据或全零。
- `run` 启动及每 5 分钟读取后端 `/api/v1/health` 的 `server_time`，结合往返耗时估计中心时间，再以 monotonic 时钟推进。报告时间为采集开始时刻，不修改系统时钟。同步失败会暂停上报并退避重试，不用失效时间或本机时钟兜底。
- 显存单位为 MiB。不可读取的指标为 `null`；进程枚举失败时 `processes_known=false`。只读 `/proc/PID/comm` 和所属 UID，不读取完整命令行、环境变量或训练参数。无权限查看其他用户的进程 metadata 时显示未知。
- 当前不支持 MIG 实例监控。MIG 已启用或无法确认 MIG 状态时，进程可见性为未知；仍尽量读取物理卡指标。
- 网络故障采用指数退避与抖动，上限约 72 秒。丢弃失败的旧样本，恢复后重新采集，避免旧的空闲状态覆盖当前数据。后端依据最后有效数据时间显示离线。
- HTTPS 会验证证书。客户端不跟随重定向发送凭据。只有本机开发可默认使用 loopback HTTP；其他 HTTP 需要登录时显式使用 `--allow-insecure-http`，不要用于当前云服务。
- 设备 token 只能上报已绑定服务器。Mac App 删除设备后，Agent 收到 401/403 会退出，systemd 不会反复重启已撤销的设备。

## 开发与显式模拟

建议为演示单独登记名为 `Demo · Simulation` 的设备并使用独立配置。先以该配置登录，再运行：

```bash
gpu-radar-agent run --config /path/demo-agent.json --simulate
# 仅上报一次；颜色判断仍需要后端累计完整 5 分钟窗口。
gpu-radar-agent run --config /path/demo-agent.json --simulate --once
```

模拟 hostname、GPU 名称和 UUID 都有 `SIMULATION` 标记，包含低负载、忙碌、占显存和未知四张卡。真实模式不会自动启用模拟。同一配置不能由两个 Agent 并发运行。

源码模式和测试，从项目根目录执行：

```bash
PYTHONPATH=agent/src python3 -m gpu_radar_agent --help
PYTHONPATH=agent/src python3 -m unittest discover -s agent/tests -v
```

NVIDIA 参考：[nvidia-smi 与 NVML](https://docs.nvidia.com/deploy/nvidia-smi/index.html)。不同驱动、发行版和机器的后台生命周期仍需在目标服务器上验证。
