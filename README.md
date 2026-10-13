# GPU Radar

在 Mac 菜单栏查看多台 Linux 服务器的 GPU 状态，快速找到可以使用的显卡。

GPU Radar 由 Linux 采集客户端、中心 API 和原生 macOS App 组成。每台服务器在菜单栏占一行：服务器名、显卡型号、每张 GPU 对应的颜色方格。方格不响应点击；利用率、显存和进程详情在桌面主窗口查看。

App 账户各自管理自己的服务器。服务器首次安装时登录 App 账户，之后使用独立设备凭据持续上报，不保存账户密码。

## 安装 Linux 客户端

先在 Mac App 注册账户，然后以日常 SSH 用户登录 GPU 服务器，运行：

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --username your_account
```

把 `your_account` 替换为 **App 账户名**。按提示输入 App 密码；密码不会回显，也不需要写进命令行。服务器名称默认取主机名，可追加 `--name lab-a`。

安装需要 Linux、Python 3.6+、可正常运行的 `nvidia-smi`、用户级 systemd，以及到 GitHub 和服务端的 HTTPS 出站连接。不要在整条安装命令前加 `sudo`。安装器会：

- 下载并校验独立 Agent，无需 pip、Docker 或额外 Python 包。
- 检查真实 GPU 采集和 HTTPS 连接，再登录并绑定账户。
- 配置每 5 秒上报的用户级 systemd 服务。
- 检查 linger 和开机启动；必要时提示管理员授权，无法确认时明确报错。
- 重复执行时保留已有绑定，不重复登记服务器；更新失败时恢复原有安装。

安装成功后，退出 SSH 仍会采集，服务器重启后自动启动。第一次接入需要连续积累 5 分钟数据，之后显示绿、黄、红状态。GPU 服务器无需新增入站端口。

只做预检，不登录、不安装后台服务：

```bash
curl -fsSL https://github.com/Yuzhaoxin946/gpu-radar/releases/latest/download/install.sh | bash -s -- --check
```

GitHub 下载不可达时，可明确使用阿里云镜像：

```bash
curl -fsSL https://123.56.135.166/agent/install.sh | bash -s -- --mirror aliyun --username your_account
```

当前发行版 App 与安装器固定连接 `https://123.56.135.166`。安装时使用自己的 App 账户；服务器之间的 Linux 用户名可以不同。

查看运行状态或最近日志：

```bash
systemctl --user status gpu-radar-agent.service
journalctl --user -u gpu-radar-agent.service -n 30 --no-pager
```

完整配置、手动安装与设备解绑说明见 [Linux Agent 文档](agent/README.md)。

## 构建 macOS App

需要 macOS 13+ 和完整 Xcode。在项目根目录执行：

```bash
cd macos
./build-app.sh
open "build/GPU Radar.app"
```

App 提供注册、登录、修改密码和演示模式。修改密码会撤销旧的 App 登录会话，已绑定 GPU 服务器继续上报。菜单栏只做概览，桌面主窗口提供服务器和 GPU 详情。

当前构建脚本使用临时签名；尚未提供 Developer ID 签名或 Apple 公证。本次 Release 提供 Linux 安装器与 Agent，Mac App 可从源码构建。

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

请先通过本地 API 创建测试账户。`--simulate` 会明确标记模拟数据；真实采集失败不会自动切换到模拟模式。

```bash
./scripts/test.sh
.venv/bin/python scripts/smoke.py
cd macos && swift test
```

更多说明：[API 契约](docs/api-contract.md) · [部署指南](docs/deployment.md) · [验证范围](docs/validation.md)。

当前聚焦个人账户与最新状态；尚未提供团队共享、长期历史、通知和完整 MIG 实例视图。
