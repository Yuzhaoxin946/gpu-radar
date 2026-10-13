# 现有 Nginx + systemd 部署与维护

本部署供已有站点和管理面板的 Linux 主机使用。使用 Python 3.11、独立 venv 和 SQLite；不安装 Docker，也不替换现有 Nginx 主配置。模板兼容 systemd 239。

## 固定路径

| 内容 | 路径 |
|---|---|
| 当前项目（可为发布目录的符号链接） | `/opt/gpu-radar/current` |
| Python 环境 | `/opt/gpu-radar/venv` |
| 环境文件（root 所有，0600） | `/etc/gpu-radar/gpu-radar.env` |
| 数据库 | `/var/lib/gpu-radar/radar.db` |
| 默认备份目录 | `/var/lib/gpu-radar/backups` |
| 服务用户与组 | `gpu-radar` |
| API 上游 | `127.0.0.1:18760` |

源码及 venv 由 root 所有；服务用户只写状态目录，目录权限 0700。源码必须让服务用户可读，且该用户不应拥有登录 shell。将 `gpu-radar.env.example` 复制到环境文件路径，`REGISTRATION_ENABLED=true` 支持桌面注册。安装依赖后，安装并验证 unit：

```bash
/opt/gpu-radar/venv/bin/python -m pip install -r /opt/gpu-radar/current/server/requirements.txt
install -m 0644 /opt/gpu-radar/current/deploy/native/gpu-radar.service /etc/systemd/system/gpu-radar.service
systemd-analyze verify /etc/systemd/system/gpu-radar.service
systemctl daemon-reload
systemctl enable --now gpu-radar
curl --fail http://127.0.0.1:18760/api/v1/health
```

已有同名账户、目录或服务时先核对，勿覆盖现有数据库或环境文件。API 使用单 worker，保持进程内登录限流一致。SQLite 已启用 WAL 和 15 秒锁等待，适合初期单实例部署；扩容前再迁移 PostgreSQL 和共享限流。

## HTTPS 入口

由既有 Nginx 提供独立 HTTPS 虚拟主机，转发 `/api/v1/` 到 `http://127.0.0.1:18760`，保留其他虚拟主机、VPN 与面板配置。`proxy_pass` 末尾不加 URI，保留请求路径。设置 `client_max_body_size 1m`，并覆盖以下请求头：

```nginx
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
```

后端只信任回环代理。如果前方增加 CDN 或其他代理，另行配置真实来源地址。Nginx 变更后先用该主机实际 Nginx 二进制执行 `-t`，再沿用原有方式 reload。

安全组仅开放实际 HTTPS 入口。API 的 18760 与数据库无需入站规则。域名或公网 IP 证书及其自动续签需由主机证书服务配置；App 与 Agent 必须能验证该证书。HTTP 80 保持证书验证路径可访问，不在公网 HTTP 上提供密码登录。

## 创建账户

桌面 App 提供注册与修改密码入口。也可以用下面的管理员命令创建账户，两次交互输入密码，不将密码放入 shell 历史或进程参数。若调整了数据库路径，也要同步此处的 `DATABASE_URL`。

```bash
cd /opt/gpu-radar/current/server
runuser -u gpu-radar -- env DATABASE_URL=sqlite:////var/lib/gpu-radar/radar.db PYTHONDONTWRITEBYTECODE=1 \
  /opt/gpu-radar/venv/bin/python -m app.manage create-user researcher
```

App 与各 GPU Agent 使用同一 HTTPS 根地址和各自账户登录。设备注册后仅保存设备凭据。

## 备份与维护

`backup.py` 使用 SQLite backup API，保留运行中 WAL 的数据，并进行完整性检查。备份权限为 0600，不覆盖已有文件，也不会自动删除历史备份。

```bash
runuser -u gpu-radar -- /opt/gpu-radar/venv/bin/python /opt/gpu-radar/current/deploy/native/backup.py
```

可选的自动备份每天按**服务器时区**约 03:15 执行，延迟最多十分钟；错过的任务会在开机后补做。

```bash
install -m 0644 /opt/gpu-radar/current/deploy/native/gpu-radar-backup.service /etc/systemd/system/
install -m 0644 /opt/gpu-radar/current/deploy/native/gpu-radar-backup.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/gpu-radar-backup.service /etc/systemd/system/gpu-radar-backup.timer
systemctl daemon-reload
systemctl enable --now gpu-radar-backup.timer
systemctl start gpu-radar-backup.service
systemctl list-timers gpu-radar-backup.timer --no-pager
```

备份含账户哈希、设备凭据哈希和监控数据，应安全同步到另一台主机，并按磁盘空间制定保留策略。不要运行中只复制 `.db` 而遗漏 WAL。恢复备份时先停服务，再替换数据库并移除属于旧数据库的 WAL/SHM，恢复正确所有权后再启动。

```bash
systemctl status gpu-radar --no-pager
journalctl -u gpu-radar --since '10 minutes ago' --no-pager
journalctl -u gpu-radar-backup.service --since yesterday --no-pager
```

升级前备份数据库并保留当前发布目录。未来数据库结构变更需要迁移步骤；切换代码目录本身不会完成迁移。暂停服务用 `systemctl stop gpu-radar`，保留数据库。
