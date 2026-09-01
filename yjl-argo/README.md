# YJL Argo Tunnel

独立的 Cloudflare Tunnel（Argo）菜单工具，面向 Debian、Ubuntu 和 Alpine。它只安装并管理 `cloudflared`，**不会安装、修改或依赖 sing-box**。

## 安装

在本目录执行：

```bash
sudo sh install.sh
```

Debian/Ubuntu 可用 `sudo bash install.sh`。Alpine 的极简镜像通常没有 Bash，使用上面的 `sh` 入口会自动安装 Bash 后继续。

之后任意目录输入：

```bash
yjl
```

首次进入选 `1`。先填入已存在的本地 HTTP / WebSocket 服务，例如 `http://127.0.0.1:8080`，再选择：

- `TryCloudflare` 临时隧道：无账号，但域名随重启变化，适合测试。
- `Token` 命名隧道：在 Cloudflare Zero Trust 创建 Cloudflared Tunnel 后粘贴 Token，再填写已经在 Dashboard 配置的完整自有域名和可选路径匹配，适合长期使用。

## 重要边界

Argo 只是“公网 HTTPS/WSS 到本机 HTTP/WebSocket 服务”的通道。本工具不会凭空提供 VLESS、VMess 或 WebSocket 后端；若本机没有对应服务，隧道能连上 Cloudflare，但访问目标仍会失败。

Token 命名隧道需在 Cloudflare Dashboard 配置 Public Hostname / 路由规则，并将服务地址设成菜单显示的同一个本地目标。菜单会记录完整域名和可选路径匹配，例如 `test.example.com` + `/123`，状态页会检查 `https://test.example.com/123`。HTTP 200、400、404 等都代表请求已到达 Cloudflare 和后端，具体协议仍应由真实客户端验证。Dashboard 的远端 Ingress 规则优先于命令行 `--url`。卸载菜单只删除本机的服务、日志和 Token 配置，不会删除 Cloudflare Dashboard 内的 Tunnel。

## 实现细节

- Debian/Ubuntu 使用 `systemd`，Alpine 使用 `OpenRC`，均已注册开机启动。
- 服务固定使用 `--no-autoupdate --protocol http2`。这避免 cloudflared 自动更新中断服务，也适用于 UDP 7844/QUIC 不稳定的网络。
- 状态页检查服务状态、cloudflared 进程和 `127.0.0.1:20241/metrics` 中的 HA 连接数。
- 本地配置位于 `/etc/yjl-argo/config.env`，权限为 `600`；Token 不会在菜单中回显。
- 若 Cloudflare 边缘域名被本地 fake-IP DNS 解析成 `198.18.x.x`，状态页会明确警告；该网络问题需由真实 DNS 或正确网关路由解决，工具不会擅自修改服务器网络。

## 验证

```bash
bash -n yjl-argo.sh
sh -n install.sh
sudo sh install.sh
yjl
```

在测试环境中可用 `yjl status` 看已保存配置和服务状态。实际可用性还应通过你要转发的真实 HTTP/WebSocket 客户端验证。
