# GPU Radar Linux Agent

在每台 GPU 服务器运行一个 Agent。首次用 **App 账户**登录并登记服务器，之后使用该设备独立的上报凭据；不用给其他 Linux 用户注册 App 账户。仅监控，不执行训练任务、不抢占 GPU。

标准 pip 安装要求 Python 3.10+；**旧服务器可以用 Python 3.6+ 直接运行下方的独立 `.pyz`，无需 pip 或额外 Python 包**。两种方式都需要 NVIDIA 驱动和服务器到阿里云后端的 HTTPS 出站访问。优先使用 `nvidia-ml-py`（NVML）；不可用时尝试 `nvidia-smi`。运行用户需要有读取 NVIDIA 设备的权限。不需要向公网开放 GPU 服务器入站端口。

## 一键安装

先在 Mac App 注册账户，以日常 SSH 用户登录 GPU 服务器后执行：

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --username your_account
```

把 `your_account` 替换为 App 账户名，按提示输入密码；可追加 `--name lab-a` 指定看板中的服务器名称。安装器固定使用当前云端服务，默认从 GitHub Release 下载已校验的 Agent。不能访问 GitHub 时，可使用阿里云镜像：

```bash
curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --username your_account
```

不要给整条命令加 `sudo`。安装器使用用户级 systemd，检查 linger 以支持 SSH 退出和开机运行；确实需要管理员权限时会给出提示。重复执行会保留已有设备绑定。只检查硬件与网络时，把账户参数换为 `--check`。

安装成功后运行 `systemctl --user status gpu-radar-agent.service` 查看状态，或 `journalctl --user -u gpu-radar-agent.service -n 30 --no-pager` 查看日志。新接入设备要积累完整 5 分钟数据后才显示颜色。

如需在自动化中准确捕获下载失败，先在 Bash 中执行 `set -o pipefail`。首次绑定需要交互式 SSH 终端；安装器不接受命令行密码。

以下为源码构建、手动安装及管理方式。

## 旧服务器：Python 3.6+ 独立文件

在开发机器上构建（从项目根目录执行；构建也只用标准库）：

```bash
python3 agent/build-zipapp.py
# 生成 agent/gpu-radar-agent.pyz，把它复制到 GPU 服务器的用户目录。
```

在 GPU 服务器上直接运行，不安装包、不更改系统 Python：

```bash
python3 gpu-radar-agent.pyz login \
  --server https://radar.example.org --name lab-a --username researcher
python3 gpu-radar-agent.pyz status
python3 gpu-radar-agent.pyz run
```

其参数、配置权限及后台行为与下方 `gpu-radar-agent` 命令完全一致。缺少 NVML Python 包时自动使用已有 `nvidia-smi`。也支持 `python3 gpu-radar-agent.pyz run --simulate --once` 做显式演示。

只检查真实硬件而不登录、不连接服务端时，执行 `python3 gpu-radar-agent.pyz collect`。标准输出只有一个 JSON 样本；成功退出码 0，采集错误也会输出错误 JSON 并以 2 退出。可传 `--collection-timeout 8` 设置硬超时。这个命令不提供模拟后备，`sampled_at` 使用本机系统时钟，仅用于诊断；正式 `run` 上报会使用下面的服务端时间同步。

用户级 systemd 模板的 `ExecStart` 改成绝对 Python 和 pyz 路径，例如 `ExecStart=/usr/bin/python3 %h/.local/share/gpu-radar/gpu-radar-agent.pyz run --config %h/.config/gpu-radar/agent.json`；不用创建 venv。系统级模板同样替换 `ExecStart`，保留已有用户及权限设置。

## 安装、登录和运行

在已复制项目的 Linux 服务器上执行，从项目根目录开始：

```bash
python3 -m venv "$HOME/.local/share/gpu-radar/venv"
"$HOME/.local/share/gpu-radar/venv/bin/pip" install './agent[nvml]'

"$HOME/.local/share/gpu-radar/venv/bin/gpu-radar-agent" login \
  --server https://radar.example.org --name lab-a --username researcher
# 在交互提示输入 App 账户密码，不要把密码写进命令行。

"$HOME/.local/share/gpu-radar/venv/bin/gpu-radar-agent" status
"$HOME/.local/share/gpu-radar/venv/bin/gpu-radar-agent" run
```

账户先在 Mac App 注册；服务端也可以关闭公开注册。Agent 登录成功后立即撤销临时账户会话，仅把设备 token、地址、服务器标识等写到 `~/.config/gpu-radar/agent.json`（权限 0600）。可通过 `--config /path/agent.json` 更改位置，放在子命令前后都可以。配置与 Agent 必须属于同一 Linux 用户。

设备 token 只能上报其已绑定服务器。Mac App 删除服务器会立即使它失效。Agent 收到 401/403 后退出，systemd 模板不会反复启动已撤销设备。`logout` 仅清理本地凭据，**还需在 Mac App 删除服务器，才能撤销服务端凭据并清理记录**。先停止后台服务，再执行 logout。

## 无 root 权限：用户级 systemd

确认上面的 venv 和默认配置已创建。仍从项目根目录执行：

```bash
mkdir -p "$HOME/.config/systemd/user"
cp agent/systemd/gpu-radar-agent.user.service \
  "$HOME/.config/systemd/user/gpu-radar-agent.service"
systemctl --user daemon-reload
systemctl --user enable --now gpu-radar-agent.service
systemctl --user status gpu-radar-agent.service
journalctl --user -u gpu-radar-agent.service -f
```

退出 SSH 后是否持续运行取决于服务器的用户会话设置。请检查：

```bash
loginctl show-user "$USER" -p Linger
```

要退出 SSH、机器重启后仍运行，通常需要管理员为该 Linux 用户开启 linger：`sudo loginctl enable-linger 用户名`。这可能需要管理员授权，不要假定你已经有权限。没有用户级 systemd 的机器，可由管理员用现有进程管理器启动相同 `run` 命令。单独用 SSH 前台运行不会保证退出连接后继续。

## 管理员安装：系统级 systemd

`systemd/gpu-radar-agent.system.service` 是模板，需要管理员调整 `User`、`Group`、`ExecStart`、`ReadWritePaths`。建议使用专门的非 root Linux 用户，并给它读取 NVIDIA 设备所需权限。

1. 管理员在 `/opt/gpu-radar/venv` 安装本包。
2. 创建 `/var/lib/gpu-radar`，归服务运行用户所有，目录权限 0700。
3. **以该服务用户**执行交互式 `login --config /var/lib/gpu-radar/agent.json ...`，验证配置 0600。
4. 将调整好的模板放入 `/etc/systemd/system/gpu-radar-agent.service`，执行 `systemctl daemon-reload` 与 `systemctl enable --now gpu-radar-agent`。

这里只提供模板，不会自动修改系统账户、sudo 配置、防火墙或服务。

## 采集语义与故障行为

- 默认每 5 秒采集；后端统一判定绿/黄/红/灰，Agent 不自行判断空闲。
- `run` 启动及每 5 分钟读取后端 `/api/v1/health` 的 `server_time`，结合请求往返耗时估计中心时间，再以 monotonic 时钟推进。报告使用采集开始时刻，因此 GPU 服务器系统时间偏差不会使样本误入未来；不会修改任何系统时钟。首次同步或定期刷新失败时暂停上报、退避重试，不使用失效锚点或本机时钟兜底。后端必须提供 `server_time`，其未来时间检测仍生效。
- NVML 在独立子进程中执行，默认 8 秒硬超时；`nvidia-smi` 单次命令限时 3 秒。驱动阻塞、无 GPU、命令失败会发送采集错误，不能悄悄变成模拟数据或全零。
- 显存单位 MiB。`N/A`、不支持、读取失败表示 `null`；进程枚举失败表示 `processes_known=false`。不读取完整命令行、环境变量或训练参数，只读 `/proc/PID/comm` 和所属 UID。其他用户的进程 metadata 无权限时为 `null`，界面可显示“未知”。
- 首版不支持 MIG 实例监控。检测到 MIG 开启，或老驱动无法确认 MIG 状态时，令进程可见性未知，防止误判空闲；仍尽量显示可读取的物理卡指标。
- 网络故障使用指数退避与抖动（上限约 72 秒）。丢弃旧样本，恢复后采集新数据，避免积压的旧“空闲”覆盖当前状态。后端依据最后有效数据时刻显示离线。
- URL 默认要求 HTTPS，允许 localhost/loopback HTTP。本机开发之外的 HTTP 只能在首次登录时显式传 `--allow-insecure-http`，该选择保存在配置。不会禁用 TLS 证书验证，也不跟随重定向发送凭据。
- `status` 仅查看本地绑定，不验证远程设备是否有效；以 Mac App 的最新数据与上报日志为准。

## 本机无 GPU 的显式演示

建议单独登记一个名为 `Demo · Simulation` 的服务器，并使用单独配置，避免混入真实机器记录。登录后运行：

```bash
gpu-radar-agent run --config /path/demo-agent.json --simulate
# 一次上报便于联调；状态判断需后端累计完整 5 分钟采样窗口。
gpu-radar-agent run --config /path/demo-agent.json --simulate --once
```

模拟 hostname、GPU 名称、UUID 都有 `SIMULATION` 标识，包含低负载、忙碌、占显存和未知四张卡。真实模式绝不自动启用它。同一配置不允许两个 Agent 并发运行。

源代码模式和测试（从项目根目录）：

```bash
PYTHONPATH=agent/src python3 -m gpu_radar_agent --help
PYTHONPATH=agent/src python3 -m unittest discover -s agent/tests -v
```

NVIDIA 参考：[nvidia-smi 与 NVML](https://docs.nvidia.com/deploy/nvidia-smi/index.html)。实际 NVIDIA 驱动、MIG、不同 Linux 发行版的服务生命周期还需在目标 GPU 机器上验收。
