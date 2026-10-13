# GPU Radar

**简体中文** | [English](README.en.md)

在 Mac 菜单栏查看多台 Linux 服务器的 GPU 状态，快速找到可以使用的显卡。

GPU Radar 由 Linux 采集客户端、中心 API 和原生 macOS App 组成。每台服务器在菜单栏占一行：服务器名、显卡型号、每张 GPU 对应的颜色方格。方格不响应点击；利用率、显存和进程详情在桌面主窗口查看。

App 账户各自管理自己的服务器。**新账户注册需要邀请码，请向服务维护者私下获取。** 邀请码只用于创建新账户；已有账户登录、修改密码和安装 Agent 均不需要邀请码。服务器首次安装时登录 App 账户，之后使用独立设备凭据持续上报，不保存账户密码。

[下载安装包](https://github.com/Yuzhaoxin946/gpu-radar/releases/latest) · [完整 Linux 安装与维护指南](agent/README.md) · [验证范围](docs/validation.md)

## 从哪里开始

1. **Mac 上注册账户**：向服务维护者获取邀请码，打开 App，在桌面主窗口填写用户名、密码和邀请码后注册并登录。还没有 App 时，先按下方[构建 macOS App](#构建-macos-app)的步骤构建。
2. **每台 GPU 服务器安装 Agent**：正常 SSH 登录服务器后，在交互式终端里运行下方安装命令；登录同一个 App 账户即可加入同一看板。
3. **回到 Mac 查看**：安装成功后每 5 秒更新一次。首次接入需要积累连续 5 分钟数据，期间 GPU 显示灰色。

## 安装 Linux 客户端

先在最新版 Mac App 中使用邀请码注册账户，然后以日常 SSH 用户登录 GPU 服务器，运行（已有账户直接安装即可）：

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --username your_account
```

把 `your_account` 替换为 **App 账户名**。按提示输入 App 密码；密码不会回显，也不需要写进命令行。服务器名称默认取主机名，可追加 `--name lab-a`。

安装需要 Linux、Bash、curl、Python 3.6+、可正常运行的 `nvidia-smi`、用户级 systemd，以及到 GitHub 和服务端的 HTTPS 出站连接。**不要在整条安装命令前加 `sudo`。** 如果安装器另行提示 sudo，它需要的是 Linux 管理权限，与 App 密码不同。安装器会：

- 下载并校验独立 Agent，无需 pip、Docker 或额外 Python 包。
- 检查真实 GPU 采集和 HTTPS 连接，再登录并绑定账户。
- 配置每 5 秒上报的用户级 systemd 服务。
- 检查 linger 和开机启动；必要时提示管理员授权，无法确认时明确报错。
- 每台主机独立保存配置与设备凭据，支持多台服务器共享 NFS 用户目录。
- 重复执行时保留本机已有绑定，不重复登记服务器；更新失败时恢复原有安装。

安装器确认首条数据被接受、服务 active/enabled 且 Linger=yes 后，退出 SSH 仍会采集，并配置为开机启动。第一次接入需要连续积累 5 分钟数据，之后显示绿、黄、红状态。GPU 服务器无需新增入站端口。

只做预检，不登录、不安装后台服务：

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --check
```

`--check` 检查下载、真实 GPU 采集和 HTTPS，不检查 systemd 或 linger 权限；这些在正式安装时检查。

GitHub 下载不可达时，可明确使用阿里云镜像：

```bash
curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --username your_account
```

两种入口提供同一发行版本，并校验 Agent 的 SHA-256。镜像命令中的 `--mirror aliyun` 必须保留，这样 Agent 包也从镜像下载。镜像预检命令：

```bash
curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --check
```

当前发行版 App 与安装器固定连接 `https://123.56.135.166`。安装时使用自己的 App 账户；服务器之间的 Linux 用户名可以不同。

首次绑定需要交互式 SSH 终端。部分跳板机的远程命令模式不会转发 TTY；请先正常登录目标 GPU 主机，再粘贴安装命令。在 Bash 自动化中，需要准确捕获管道下载失败时，先执行 `set -o pipefail`。

## 日常查看与维护

以下命令适用于当前一键安装器。在目标 GPU 服务器上，以安装时的 Linux 用户查看状态、最近日志和开机运行设置：

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
unit="gpu-radar-agent-$(cat /etc/machine-id).service"
systemctl --user status "$unit"
journalctl --user -u "$unit" -n 30 --no-pager
loginctl show-user "$(id -un)" -p Linger
```

需要重启采集时，使用上面设置的变量执行 `systemctl --user restart "$unit"`。

更新或重试时，重新执行原安装命令即可保留本机已有绑定，不会重复登记服务器。`--username` 和 `--name` 不是改绑、改名选项；已有绑定与参数不一致时会停止。

共享 NFS Home 的主机必须各自拥有有效且唯一的 `/etc/machine-id`。程序、配置、锁和服务按主机隔离。旧版手动安装的 `gpu-radar-agent.service` 保持原样；安装器遇到旧版平铺配置或服务会停止，需核实所属主机后迁移。

停止采集、卸载、撤销设备凭据、详细路径和手动安装说明见 [Linux Agent 文档](agent/README.md)。

## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| 邀请码无效或已用完 | 联系服务维护者获取有效邀请码；每成功创建一个新账户才消耗一个名额，安装 GPU Agent 不消耗名额 |
| 注册界面没有邀请码输入框 | 更新仓库并重新构建 Mac App；旧版仍可登录已有账户 |
| GitHub 下载超时 | 使用阿里云镜像命令，并保留 `--mirror aliyun` |
| 提示需要交互式终端 | 先正常 SSH 登录目标 GPU 主机，再粘贴命令，尤其是经跳板机连接时 |
| linger 或用户服务不可用 | 按安装器提示请管理员协助，然后重新 SSH 登录并重试；成功前不能认为已配置开机运行 |
| `Unsafe directory` | 核实错误中具体目录的属主和权限，仅针对确认归本人管理的目录修复；不要递归修改整个 Home 的权限，详见 Linux 指南 |
| 检测到 legacy 配置 | 保留旧配置和服务，先核实所属主机；不要复制设备凭据到其他服务器或直接删除配置来绕过检查 |
| GPU 一直显示灰色 | 首次等待连续 5 分钟；之后仍灰色时，查看采集日志、网络和最近上报情况 |
| 修改 App 密码后是否要重登 Agent？ | 不需要。设备继续使用独立凭据；旧 App 登录会话会失效 |

## 构建 macOS App

需要 macOS 13+、完整 Xcode 和 Swift 5.9+。在 **Mac** 上执行：

```bash
git clone https://github.com/Yuzhaoxin946/gpu-radar.git
cd gpu-radar
GPU_RADAR_BUILD_WORK="$PWD/.build/macos-build" ./macos/build-app.sh
open "macos/build/GPU Radar.app"
```

已经克隆仓库时，从项目根目录执行后两条命令即可。输出为 `macos/build/GPU Radar.app`，可以将它拖入“应用程序”目录，方便以后打开。

App 提供邀请码注册、登录、修改密码和演示模式。修改密码会撤销旧的 App 登录会话，已绑定 GPU 服务器继续上报。菜单栏只做概览，桌面主窗口提供服务器和 GPU 详情。

当前构建脚本使用临时签名，生成当前 Mac 架构的 App；尚未提供 Developer ID 签名或 Apple 公证。GitHub Release 的二进制附件目前仅提供 Linux 安装器与 Agent，不含可直接下载的 Mac 安装包。

## 颜色含义

| 颜色 | 状态 | 判断条件 |
| --- | --- | --- |
| 绿色 | 空闲 | 最近 5 分钟平均利用率 <10%，且当前剩余显存 >90% |
| 黄色 | 被占用 | 最近 5 分钟平均利用率 <10%，且当前剩余显存 ≤90% |
| 红色 | 使用中 | 最近 5 分钟平均利用率 ≥10% |
| 灰色 | 数据不足或失效 | 窗口不足 5 分钟、指标不可用、采集失败或超过 30 秒未更新 |

平均值按服务端实际收样间隔加权，保存在数据库中。采样断档或利用率无效会重新积累窗口；无法读取的指标不会被当成零。进程信息只用于详情展示。

绿色表示监控结果，不是 GPU 预留。GPU Radar 不执行训练任务、不抢占显卡、不终止进程，也不提供作业调度。

## 项目结构

| 目录 | 内容 |
| --- | --- |
| `agent/` | Python 3.6+ 采集代码、安装器、独立包构建、systemd 模板 |
| `server/` | FastAPI、账户与设备认证、数据库和状态计算 |
| `macos/` | SwiftUI 桌面 App 与菜单栏界面 |
| `deploy/` | 原生 systemd / Nginx 和可选 Docker Compose 配置 |
| `scripts/` | 本地开发、测试与端到端联调脚本 |
| `docs/` | API 契约、部署指南和验证范围 |

## 本地开发与测试

后端开发需要 Python 3.11+。从项目根目录运行：

```bash
./scripts/setup-local.sh
./scripts/start-local.sh
```

本地 API 监听 `http://127.0.0.1:8000`，接口文档位于 `/docs`，数据库保存在 `.local/radar.db`。发布版 Mac App 使用固定云端地址；本地 API 可与 Agent CLI 联调。

```bash
.venv/bin/gpu-radar-agent --config .local/demo-agent.json login \
  --server http://127.0.0.1:8000 --name demo-lab --username researcher
.venv/bin/gpu-radar-agent --config .local/demo-agent.json run --simulate
```

请先按照 [服务端账户管理说明](server/README.md#invitation-only-registration)创建本地邀请码，再通过本地 API 注册测试账户；也可以用管理员命令直接创建测试账户。`--simulate` 会明确标记模拟数据；真实采集失败不会自动切换到模拟模式。

```bash
./scripts/test.sh
.venv/bin/python scripts/smoke.py
cd macos && swift test
```

更多说明：[API 契约](docs/api-contract.md) · [部署指南](docs/deployment.md) · [验证范围](docs/validation.md)。

项目主页和 Linux 安装指南均提供中英文版本；其余维护文档沿用各自现有语言。

当前聚焦个人账户与最新状态；尚未提供团队共享、长期历史、通知和完整 MIG 实例视图。
