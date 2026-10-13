# 服务端部署

GPU Radar 支持原生 systemd / Nginx / SQLite，以及可选的 Docker Compose / PostgreSQL / Caddy 部署。原生方案见 [部署模板说明](../deploy/native/README.md)；以下描述在新主机上部署 Compose 的步骤。

当前发行版 Mac App 与一键安装器固定连接 `https://123.56.135.166`。自行部署其他地址时，需要修改并重新构建 Mac App 中的 `API.baseURL` 与固定来源检查，同时修改安装器中的服务端地址；Agent CLI 本身接受 `--server`。仅更改云端配置不会让现有 App 自动切换地址。

## 准备

需要 Linux 主机、Docker Engine 与 Compose 插件，以及已解析到主机的域名。将项目复制到专用目录，先确认端口没有被其他服务占用。

安全组允许 HTTPS 443，以及证书签发和跳转需要的 HTTP 80。数据库和 API 保持内部可见。GPU 服务器只需发起出站 HTTPS 连接，无需新增入站规则。

## 启动 Compose

在项目根目录执行：

```bash
cp deploy/.env.example deploy/.env
openssl rand -hex 32
```

编辑 `deploy/.env`：`RADAR_DOMAIN` 只写域名，不含 `https://`；`POSTGRES_PASSWORD` 填入刚生成的十六进制字符串。不要使用示例密码，也不要提交实际环境文件。

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build
docker compose --env-file deploy/.env -f deploy/compose.yaml exec api \
  python -m app.manage create-user researcher
```

管理员命令会交互输入账户密码。`REGISTRATION_ENABLED=true` 时，App 的桌面登录页允许凭有效邀请码注册；邀请码由管理员私下分发，成功创建新账户才消耗一个名额。创建方法见[邀请码管理](../server/README.md#invitation-only-registration)。改为 `false` 可完全关闭自助注册，仅允许管理员创建账户。

```bash
curl --fail https://radar.example.com/api/v1/health
```

Caddy 根据配置的域名申请证书，需要正确的 DNS 与公网 80/443 连通。客户端必须正常验证证书，不要通过关闭 TLS 验证绕过部署问题。

## 维护与备份

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
docker compose --env-file deploy/.env -f deploy/compose.yaml logs --tail 100 api
```

API 使用一个 worker；登录限流保存在进程内。扩容到多 worker 或多副本前，需要共享限流存储。API 仅接受 Compose 内网连接并信任 Caddy 的来源地址，不要再把 API 的 8000 端口直接发布到公网。

数据库包含账户哈希、设备凭据哈希和监控数据。在部署主机备份：

```bash
mkdir -p backups
umask 077
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T db \
  pg_dump -U radar -d radar -Fc > "backups/radar-$(date +%Y%m%d-%H%M%S).dump"
```

数据保存在 Compose 具名卷中。普通重启保留数据；`down -v` 会删除数据库，不应用于日常升级。升级前备份；未来数据库结构变更须配套迁移步骤。

## 验证边界

原生 Python / systemd / Nginx / SQLite 部署及 Linux Agent 的真实公网 HTTPS 上报已经验证。Compose 配置尚未在运行中的 Docker / PostgreSQL / Caddy 环境完成端到端验收。

参考：[FastAPI 容器部署](https://fastapi.tiangolo.com/deployment/docker/) · [Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)。
